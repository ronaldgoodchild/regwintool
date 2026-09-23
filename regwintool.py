import sys
import os
import re
import winreg
import shutil
import json
import ctypes
import time
import queue
import threading
from datetime import datetime
from PyQt5.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout,
                             QHBoxLayout, QPushButton, QLabel,
                             QTextEdit, QMessageBox, QLineEdit,
                             QAction, QStatusBar, QProgressBar, QTableWidget,
                             QTableWidgetItem, QCheckBox, QGroupBox, QComboBox,
                             QFileDialog, QSplitter, QFrame, QScrollArea, QInputDialog)
from PyQt5.QtCore import Qt, QThread, pyqtSignal
from PyQt5.QtGui import QFont, QColor, QIcon
import subprocess

DEFAULT_SETTINGS = {
    'auto_backup_before_fix': True,
    'skip_system_components': True,
    'launch_as_admin': False,
    'remember_last_page': False,
    'auto_scan_registry_on_launch': False,
    'font_size': 10,
    'last_page': 'dashboard',
    'file_backup_jobs': [],
}

def get_data_dir():
    """Folder REGwintool stores its own data in - backups and settings."""
    return os.path.join(os.environ.get('USERPROFILE', 'C:\\'), 'REGwintool_Backups')

def load_settings():
    """Load saved settings, falling back to defaults for anything missing
    or if the file doesn't exist yet / is corrupt."""
    settings = dict(DEFAULT_SETTINGS)
    path = os.path.join(get_data_dir(), 'settings.json')
    try:
        with open(path, 'r', encoding='utf-8') as f:
            settings.update(json.load(f))
    except Exception:
        pass
    return settings

def save_settings(settings):
    """Persist settings to disk - best-effort, never raises."""
    try:
        os.makedirs(get_data_dir(), exist_ok=True)
        path = os.path.join(get_data_dir(), 'settings.json')
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(settings, f, indent=2)
    except Exception as e:
        print(f"Could not save settings: {e}")

# One-click Windows tweaks for the Tools page - adapted from TechniciansToolkit_V30.
# Each is a plain reg.exe (or sc/taskkill) one-liner, applied together via '&'.
WINDOWS_TWEAKS = [
    {'name': 'Enable Dark Theme (Apps + System)', 'admin': False, 'restart_explorer': False, 'commands': [
        'reg add "HKCU\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Themes\\Personalize" /v AppsUseLightTheme /t REG_DWORD /d 0 /f',
        'reg add "HKCU\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Themes\\Personalize" /v SystemUsesLightTheme /t REG_DWORD /d 0 /f',
    ]},
    {'name': 'Show File Extensions', 'admin': False, 'restart_explorer': True, 'commands': [
        'reg add "HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Explorer\\Advanced" /v HideFileExt /t REG_DWORD /d 0 /f',
    ]},
    {'name': 'Show Hidden Files', 'admin': False, 'restart_explorer': True, 'commands': [
        'reg add "HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Explorer\\Advanced" /v Hidden /t REG_DWORD /d 1 /f',
    ]},
    {'name': 'Remove Start Menu Ads/Suggestions', 'admin': False, 'restart_explorer': False, 'commands': [
        'reg add "HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\ContentDeliveryManager" /v SubscribedContent-338388Enabled /t REG_DWORD /d 0 /f',
        'reg add "HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\ContentDeliveryManager" /v SystemPaneSuggestionsEnabled /t REG_DWORD /d 0 /f',
    ]},
    {'name': 'Disable Web Search In Start Menu', 'admin': False, 'restart_explorer': False, 'commands': [
        'reg add "HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Search" /v BingSearchEnabled /t REG_DWORD /d 0 /f',
    ]},
    {'name': 'Classic Right-Click Menu (Windows 11)', 'admin': False, 'restart_explorer': True, 'commands': [
        'reg add "HKCU\\Software\\Classes\\CLSID\\{86ca1aa0-34aa-4e8b-a509-50c905bae2a2}\\InprocServer32" /ve /f',
    ]},
    {'name': 'Disable Telemetry & Data Collection (Admin)', 'admin': True, 'restart_explorer': False, 'commands': [
        'reg add "HKLM\\SOFTWARE\\Policies\\Microsoft\\Windows\\DataCollection" /v AllowTelemetry /t REG_DWORD /d 0 /f',
        'sc config DiagTrack start= disabled',
        'sc stop DiagTrack',
    ]},
    {'name': 'Disable Cortana (Admin)', 'admin': True, 'restart_explorer': False, 'commands': [
        'reg add "HKLM\\SOFTWARE\\Policies\\Microsoft\\Windows\\Windows Search" /v AllowCortana /t REG_DWORD /d 0 /f',
    ]},
    {'name': 'Disable Animations (performance)', 'admin': False, 'restart_explorer': False, 'commands': [
        'reg add "HKCU\\Control Panel\\Desktop\\WindowMetrics" /v MinAnimate /t REG_SZ /d 0 /f',
    ]},
    {'name': 'Enable Game Mode', 'admin': False, 'restart_explorer': False, 'commands': [
        'reg add "HKCU\\Software\\Microsoft\\GameBar" /v AllowAutoGameMode /t REG_DWORD /d 1 /f',
    ]},
    {'name': 'Disable Game Bar', 'admin': False, 'restart_explorer': False, 'commands': [
        'reg add "HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\GameDVR" /v AppCaptureEnabled /t REG_DWORD /d 0 /f',
    ]},
    {'name': 'Disable Fullscreen Optimizations', 'admin': False, 'restart_explorer': False, 'commands': [
        'reg add "HKCU\\System\\GameConfigStore" /v GameDVR_FSEBehaviorMode /t REG_DWORD /d 2 /f',
    ]},
    {'name': 'Optimize Network Latency (Admin)', 'admin': True, 'restart_explorer': False, 'commands': [
        'reg add "HKLM\\SOFTWARE\\Microsoft\\Windows NT\\CurrentVersion\\Multimedia\\SystemProfile" /v NetworkThrottlingIndex /t REG_DWORD /d 0xffffffff /f',
        'reg add "HKLM\\SOFTWARE\\Microsoft\\Windows NT\\CurrentVersion\\Multimedia\\SystemProfile" /v SystemResponsiveness /t REG_DWORD /d 0 /f',
    ]},
    {'name': 'Remove OneDrive (Admin, Destructive)', 'admin': True, 'restart_explorer': False, 'destructive': True, 'commands': [
        'taskkill /f /im OneDrive.exe',
        '%SystemRoot%\\SysWOW64\\OneDriveSetup.exe /uninstall',
    ]},
]

def is_admin():
    """Proof-positive admin check: try to open a protected HKLM key for
    write access.  If Windows allows it, the process is genuinely elevated.
    No token-inspection guesswork that varies by UAC policy."""
    try:
        key = winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SYSTEM\CurrentControlSet",
            0,
            winreg.KEY_READ | winreg.KEY_WRITE
        )
        winreg.CloseKey(key)
        return True
    except Exception:
        return False

def run_as_admin():
    """Re-launch this script elevated via the Windows UAC runas verb."""
    try:
        script = os.path.abspath(__file__)
        params = f'"{script}"'
        ret = ctypes.windll.shell32.ShellExecuteW(
            None, "runas", sys.executable, params, None, 1
        )
        return ret > 32   # >32 means the elevated process started
    except Exception as e:
        print(f"Could not restart as admin: {e}")
        return False

def recursive_delete_key(hive, key_path):
    """Delete a registry key and all its subkeys.

    Windows refuses to delete a key that still has children, and 64-bit
    processes get silently redirected to WOW6432Node unless told not to be
    - so every open here uses KEY_ALL_ACCESS | KEY_WOW64_64KEY and clears
    children first."""
    flags = winreg.KEY_ALL_ACCESS | winreg.KEY_WOW64_64KEY
    try:
        with winreg.OpenKey(hive, key_path, 0, flags) as key:
            while True:
                try:
                    subkey_name = winreg.EnumKey(key, 0)
                except OSError:
                    break
                if not recursive_delete_key(key, subkey_name):
                    return False
        winreg.DeleteKeyEx(hive, key_path, winreg.KEY_WOW64_64KEY, 0)
        return True
    except FileNotFoundError:
        return True   # already gone
    except PermissionError:
        return False   # likely protected by TrustedInstaller
    except OSError:
        return False

def command_target_exists(command_string):
    """Check whether the program a registry command string points to can
    actually be found.

    Naively checking os.path.exists() on the first whitespace-split token
    gives false positives for the huge share of uninstall/run commands that
    aren't a bare absolute path - MSI-based installs use
    "MsiExec.exe /X{GUID}" (a bare filename resolved via PATH, not the
    working directory), and some entries store unexpanded env vars like
    "%ProgramFiles%\\...". Both would wrongly look "missing" otherwise."""
    if not command_string or not isinstance(command_string, str):
        return False
    value = os.path.expandvars(command_string.strip())
    if not value:
        return False
    if value.startswith('"'):
        end = value.find('"', 1)
        exe = value[1:end] if end != -1 else value[1:]
    else:
        parts = value.split()
        exe = parts[0] if parts else ''
    if not exe:
        return False
    if os.path.isabs(exe):
        return os.path.exists(exe)
    # Bare command name (MsiExec.exe, rundll32.exe, ...) - resolve via PATH
    return shutil.which(exe) is not None

class ScanThread(QThread):
    """Thread for background scanning"""
    progress = pyqtSignal(str, int)
    finished = pyqtSignal(list)
    
    def __init__(self, scan_function):
        super().__init__()
        self.scan_function = scan_function
    
    def run(self):
        try:
            results = self.scan_function(self.progress.emit)
            self.finished.emit(results)
        except Exception as e:
            print(f"Scan error: {e}")
            self.finished.emit([])

class CleanThread(QThread):
    """Thread for background cleaning"""
    progress = pyqtSignal(str, int)
    finished = pyqtSignal(dict)
    
    def __init__(self, clean_function, items):
        super().__init__()
        self.clean_function = clean_function
        self.items = items
    
    def run(self):
        try:
            results = self.clean_function(self.items, self.progress.emit)
            self.finished.emit(results)
        except Exception as e:
            print(f"Clean error: {e}")
            self.finished.emit({'success': 0, 'failed': 0, 'size_freed': 0})

class UninstallThread(QThread):
    """Runs a queue of program uninstallers one at a time, waiting for each
    wizard to be closed before starting the next - launching them all at
    once risks two installers fighting over the same MSI lock."""
    progress = pyqtSignal(str, int)
    finished = pyqtSignal(dict)

    def __init__(self, programs):
        super().__init__()
        self.programs = programs

    def run(self):
        launched = 0
        failed = []
        total = len(self.programs)
        for i, prog in enumerate(self.programs):
            self.progress.emit(f"Uninstalling {i + 1} of {total}: {prog['name']}", int(i / total * 100))
            try:
                proc = subprocess.Popen(prog['uninstall_string'], shell=True)
                proc.wait()
                launched += 1
            except Exception as e:
                failed.append(f"{prog['name']}: {e}")
        self.finished.emit({'launched': launched, 'failed': failed})

class FileMirrorThread(QThread):
    """Runs a queue of robocopy /MIR jobs one at a time (adapted from the
    BackupEngine.sync_folders logic in the standalone BackupPro3 app).
    Each job makes `destination` an exact mirror of `source` - anything in
    destination that isn't in source gets deleted. Used for both backup
    (source=original, destination=backup copy) and restore (reversed)."""
    progress = pyqtSignal(str, int)
    finished = pyqtSignal(dict)

    def __init__(self, jobs):
        super().__init__()
        self.jobs = jobs   # list of (source, destination) tuples
        self.should_cancel = False

    def cancel(self):
        self.should_cancel = True

    def run(self):
        results = []
        total = len(self.jobs)
        for i, (source, destination) in enumerate(self.jobs):
            self.progress.emit(f"Mirroring {i + 1} of {total}: {source}", int(i / max(total, 1) * 100))
            entry = {'source': source, 'destination': destination, 'success': False, 'error': ''}
            try:
                os.makedirs(destination, exist_ok=True)
                cmd = [
                    'robocopy', source, destination,
                    '/MIR', '/Z', '/R:2', '/W:5', '/MT:16', '/NP', '/NDL', '/NFL'
                ]
                process = subprocess.Popen(
                    cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                    creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0
                )
                while process.poll() is None:
                    if self.should_cancel:
                        process.terminate()
                        entry['error'] = 'Cancelled'
                        results.append(entry)
                        self.finished.emit({'results': results, 'cancelled': True})
                        return
                    self.msleep(300)
                # Robocopy exit codes 0-7 are success (including "files copied" etc), 8+ are errors
                if process.returncode < 8:
                    entry['success'] = True
                else:
                    stderr = process.stderr.read() if process.stderr else ''
                    entry['error'] = f'robocopy exit code {process.returncode} {stderr[:200]}'.strip()
            except Exception as e:
                entry['error'] = str(e)
            results.append(entry)
        self.progress.emit('File backup complete', 100)
        self.finished.emit({'results': results, 'cancelled': False})

# Matches ANSI/VT100 escape sequences (e.g. "\x1b[32;1m", "\x1b[0m") that
# PowerShell 7's colorized Format-List/Format-Table output emits even when
# piped to a non-terminal - a plain QTextEdit can't render them, so without
# stripping this, output looks like "[32;1mCaption : [0mWindows 11".
ANSI_ESCAPE_RE = re.compile(r'\x1b\[[0-9;]*[a-zA-Z]')

def strip_ansi(text):
    return ANSI_ESCAPE_RE.sub('', text)

class ConsoleLineDecoder:
    """Turns a raw byte stream from a subprocess into decoded text lines.

    sfc.exe (and some other legacy console tools) write UTF-16LE even when
    their output is redirected to a pipe instead of a real console. Reading
    that byte-wise with a plain readline() is unsafe: '\\n' encodes as
    bytes [0x0A, 0x00], so a byte-oriented readline() call stops AT the
    0x0A and leaves the trailing 0x00 sitting at the start of the next
    read - shifting every following character pair out of alignment and
    producing garbage. This buffers raw bytes, decides UTF-16LE vs the
    normal single-byte console codepage once there's enough data to tell,
    decodes complete character pairs only (holding back a trailing odd
    byte), and splits into lines AFTER decoding, not before."""

    def __init__(self):
        self.byte_buffer = b''
        self.text_buffer = ''
        self.encoding = None

    def feed(self, chunk):
        """Feed raw bytes in; returns a list of complete decoded lines."""
        self.byte_buffer += chunk

        if self.encoding is None:
            if len(self.byte_buffer) < 8:
                return []
            sample = self.byte_buffer[:64]
            self.encoding = 'utf-16-le' if sample.count(b'\x00') > len(sample) // 3 else 'mbcs'

        if self.encoding == 'utf-16-le':
            usable_len = len(self.byte_buffer) - (len(self.byte_buffer) % 2)
        else:
            usable_len = len(self.byte_buffer)
        decode_bytes, self.byte_buffer = self.byte_buffer[:usable_len], self.byte_buffer[usable_len:]

        try:
            self.text_buffer += decode_bytes.decode(self.encoding, errors='replace')
        except LookupError:
            self.text_buffer += decode_bytes.decode('utf-8', errors='replace')

        # Split on '\n' OR '\r' - tools like sfc.exe redraw a progress
        # percentage in place using bare '\r' with no '\n', so treating
        # only '\n' as a line break would glue hundreds of updates into
        # one unreadable line instead of showing live progress.
        lines = []
        while True:
            idx_n = self.text_buffer.find('\n')
            idx_r = self.text_buffer.find('\r')
            candidates = [i for i in (idx_n, idx_r) if i != -1]
            if not candidates:
                break
            idx = min(candidates)
            lines.append(self.text_buffer[:idx])
            # treat a "\r\n" pair as a single terminator
            skip = 2 if self.text_buffer[idx:idx + 2] == '\r\n' else 1
            self.text_buffer = self.text_buffer[idx + skip:]
        return lines

    def flush(self):
        """Call once the process has exited to get any trailing partial line."""
        leftover = self.text_buffer.rstrip('\r\n')
        self.text_buffer = ''
        return [leftover] if leftover else []

class CommandThread(QThread):
    """Runs a sequence of maintenance/repair steps in the background and
    streams a line per step to the UI. Each step is either a
    (description, shell_command, timeout_seconds) tuple, or a plain
    callable(output_emit) for steps that need real logic instead of a
    single command (e.g. looping over a DLL list). Used for the Repair
    page's multi-step Windows Update / Defender / SFC-DISM operations,
    adapted from the standalone TechniciansToolkit app - these chain many
    sub-commands where an individual non-zero exit (e.g. "net stop" on an
    already-stopped service) is routine, not fatal, so failures are logged
    but never abort the sequence."""
    output = pyqtSignal(str)
    finished = pyqtSignal()

    def __init__(self, steps):
        super().__init__()
        self.steps = steps

    def run(self):
        for step in self.steps:
            if callable(step):
                try:
                    step(self.output.emit)
                except Exception as e:
                    self.output.emit(f'  Error: {e}')
                continue

            description, command, timeout = step
            self.output.emit(description)
            try:
                # Popen + incremental chunked reads (not subprocess.run) so
                # long commands like "winget upgrade --all" or "sfc /scannow"
                # stream their output live instead of dumping it all at once
                # after they finish minutes later. Raw binary mode, decoded
                # through ConsoleLineDecoder so UTF-16LE tools (sfc.exe)
                # come out readable instead of garbled.
                process = subprocess.Popen(
                    command, shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    bufsize=0,
                    creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0
                )
                decoder = ConsoleLineDecoder()

                # Read on a daemon thread via a queue instead of calling
                # process.stdout.read() directly on this thread. A direct
                # read() can block forever: if this command starts a
                # long-running detached process (e.g. a tweak's
                # "start explorer.exe" to restart the shell), that
                # grandchild inherits the pipe's write handle, so the pipe
                # never signals EOF even though our actual child (cmd.exe)
                # exited ages ago - polling process.poll() between queue
                # gets lets us recognize "our command is done" instead of
                # hanging on a handle some unrelated long-lived process
                # still holds open.
                read_queue = queue.Queue()

                def _reader(proc=process, q=read_queue):
                    try:
                        while True:
                            chunk = proc.stdout.read(4096)
                            q.put(chunk)
                            if not chunk:
                                break
                    except Exception:
                        q.put(b'')

                reader_thread = threading.Thread(target=_reader, daemon=True)
                reader_thread.start()

                start_time = time.time()
                timed_out = False
                reached_eof = False
                while True:
                    try:
                        chunk = read_queue.get(timeout=0.5)
                    except queue.Empty:
                        chunk = None

                    if chunk:
                        for line in decoder.feed(chunk):
                            line = strip_ansi(line)
                            if line:
                                self.output.emit(line)
                    elif chunk == b'':
                        reached_eof = True
                        break  # real EOF - process and everything holding the pipe have closed it

                    if time.time() - start_time > timeout:
                        timed_out = True
                        process.terminate()
                        break

                    if chunk is None and process.poll() is not None:
                        # Our child exited and nothing new arrived in the last
                        # 0.5s - a lingering grandchild (e.g. a freshly
                        # restarted explorer.exe) is the only thing still
                        # holding the pipe open. Stop waiting on it - but
                        # the reader thread's read() call is then stuck on
                        # that handle forever, so anything that would block
                        # on it too (like closing this end of the pipe,
                        # below) must be skipped.
                        break

                for line in decoder.flush():
                    line = strip_ansi(line)
                    if line:
                        self.output.emit(line)
                if reached_eof:
                    # Only safe to close/join here - the reader thread has
                    # already returned, so this can't deadlock waiting on
                    # a lock the reader is still holding mid-read().
                    try:
                        process.stdout.close()
                    except Exception:
                        pass
                if timed_out:
                    self.output.emit('  (step timed out - continuing)')
                elif reached_eof:
                    try:
                        process.wait(timeout=5)
                    except Exception:
                        pass
            except Exception as e:
                self.output.emit(f'  Error: {e}')
        self.finished.emit()

class WinToolsApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.scan_results = []
        self.file_results = []
        self.startup_items = []
        self.installed_programs = []
        self.current_page_id = 'dashboard'
        self.scan_thread = None
        self.clean_thread = None
        self.uninstall_thread = None
        self.file_mirror_thread = None
        self.command_thread = None
        self.settings = load_settings()
        self.backup_dir = self.settings.get('backup_dir') or get_data_dir()
        self.is_admin = is_admin()
        
        # Initialize activity_log as None - will be created in createDashboardPage
        self.activity_log = None
        
        # Create backup directory
        if not os.path.exists(self.backup_dir):
            try:
                os.makedirs(self.backup_dir)
            except Exception as e:
                print(f"Could not create backup directory: {e}")
        
        self.initUI()
    
    def initUI(self):
        """Initialize the user interface"""
        admin_status = "ADMIN" if self.is_admin else "USER"
        self.setWindowTitle(f'REGwintool v2.1 - {admin_status} MODE')
        self.setGeometry(50, 50, 1200, 800)
        self.setMinimumSize(1000, 700)
        
        # Create menu bar
        self.createMenuBar()
        
        # Create central widget
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        
        # Main layout
        main_layout = QVBoxLayout()
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)
        central_widget.setLayout(main_layout)
        
        # Admin warning banner (if not admin)
        if not self.is_admin:
            admin_warning = QLabel('WARNING: USER MODE - Some features require Administrator rights')
            admin_warning.setStyleSheet("""
                QLabel {
                    background-color: #fab387;
                    color: #11111b;
                    padding: 8px;
                    font-size: 13px;
                    font-weight: bold;
                }
            """)
            admin_warning.setAlignment(Qt.AlignCenter)
            main_layout.addWidget(admin_warning)
            
            # Add "Run as Admin" button
            admin_btn_container = QWidget()
            admin_btn_layout = QHBoxLayout()
            admin_btn_container.setLayout(admin_btn_layout)
            admin_btn_layout.setContentsMargins(10, 5, 10, 5)
            
            admin_btn = QPushButton("Restart as Administrator")
            admin_btn.setFixedHeight(35)
            admin_btn.setFixedWidth(250)
            admin_btn.clicked.connect(self.restartAsAdmin)
            admin_btn.setStyleSheet("""
                QPushButton {
                    background-color: #fab387;
                    color: #11111b;
                    font-size: 13px;
                    padding: 8px;
                }
                QPushButton:hover {
                    background-color: #f5a26a;
                }
            """)
            admin_btn_layout.addStretch()
            admin_btn_layout.addWidget(admin_btn)
            admin_btn_layout.addStretch()
            main_layout.addWidget(admin_btn_container)
        
        # Create horizontal splitter for sidebar and content
        splitter = QSplitter(Qt.Horizontal)
        main_layout.addWidget(splitter)
        
        # LEFT SIDEBAR - Navigation
        sidebar = self.createSidebar()
        splitter.addWidget(sidebar)
        
        # RIGHT CONTENT AREA - use QStackedWidget for reliable page switching
        from PyQt5.QtWidgets import QStackedWidget
        self.content_stack = QStackedWidget()
        splitter.addWidget(self.content_stack)
        
        # Set splitter sizes (sidebar 200px, content gets rest)
        splitter.setSizes([200, 1000])
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)

        # Status bar (must be created before pages so page init methods can use it)
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        status_text = 'Admin Mode Active' if self.is_admin else 'User Mode - Limited features'
        self.status_bar.showMessage(status_text)

        # Progress bar
        self.progress_bar = QProgressBar()
        self.progress_bar.setMaximumWidth(200)
        self.progress_bar.setMaximumHeight(18)
        self.status_bar.addPermanentWidget(self.progress_bar)
        self.progress_bar.setVisible(False)

        # Create all content pages and add to stacked widget
        page_order = [
            ('dashboard', self.createDashboardPage),
            ('registry', self.createRegistryPage),
            ('files', self.createFilesPage),
            ('startup', self.createStartupPage),
            ('privacy', self.createPrivacyPage),
            ('uninstaller', self.createUninstallerPage),
            ('repair', self.createRepairPage),
            ('tools', self.createToolsPage),
            ('backups', self.createBackupsPage),
            ('settings', self.createSettingsPage),
        ]
        self.pages = {}
        for page_id, creator in page_order:
            page = creator()
            self.pages[page_id] = page
            self.content_stack.addWidget(page)

        # Show the last-viewed page if that's enabled, otherwise the dashboard
        start_page = 'dashboard'
        if self.settings.get('remember_last_page') and self.settings.get('last_page') in self.pages:
            start_page = self.settings['last_page']
        self.showPage(start_page)

        # Apply global dark stylesheet
        self.setStyleSheet("""
            QMainWindow, QWidget {
                background-color: #1e1e2e;
                color: #cdd6f4;
            }
            QPushButton {
                background-color: #3498db;
                color: #ffffff;
                border: none;
                padding: 8px 15px;
                border-radius: 4px;
                font-weight: bold;
                font-size: 12px;
            }
            QPushButton:hover {
                background-color: #2980b9;
            }
            QPushButton:pressed {
                background-color: #1f6391;
            }
            QPushButton:disabled {
                background-color: #45475a;
                color: #6c7086;
            }
            QGroupBox {
                font-weight: bold;
                border: 1px solid #45475a;
                border-radius: 5px;
                margin-top: 10px;
                padding-top: 10px;
                background-color: #181825;
                color: #cdd6f4;
            }
            QGroupBox::title {
                color: #89b4fa;
                subcontrol-origin: margin;
                padding: 0 5px;
            }
            QTableWidget {
                border: 1px solid #45475a;
                background-color: #181825;
                alternate-background-color: #1e1e2e;
                gridline-color: #313244;
                color: #cdd6f4;
            }
            QTableWidget::item {
                padding: 5px;
                color: #cdd6f4;
            }
            QTableWidget::item:selected {
                background-color: #3498db;
                color: #ffffff;
            }
            QHeaderView::section {
                background-color: #313244;
                color: #89b4fa;
                padding: 5px;
                border: none;
                font-weight: bold;
            }
            QTextEdit {
                border: 1px solid #45475a;
                background-color: #181825;
                color: #cdd6f4;
                font-family: Consolas, monospace;
                font-size: 11px;
            }
            QLabel {
                color: #cdd6f4;
            }
            QScrollArea {
                border: none;
                background-color: #1e1e2e;
            }
            QScrollBar:vertical {
                background: #313244;
                width: 8px;
                border-radius: 4px;
            }
            QScrollBar::handle:vertical {
                background: #585b70;
                border-radius: 4px;
                min-height: 20px;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                height: 0px;
            }
            QStatusBar {
                background-color: #181825;
                color: #a6adc8;
            }
            QProgressBar {
                border: 1px solid #45475a;
                border-radius: 3px;
                background-color: #313244;
                color: #cdd6f4;
            }
            QProgressBar::chunk {
                background-color: #3498db;
                border-radius: 3px;
            }
            QMenuBar {
                background-color: #181825;
                color: #cdd6f4;
            }
            QMenuBar::item:selected {
                background-color: #313244;
            }
            QMenu {
                background-color: #1e1e2e;
                color: #cdd6f4;
                border: 1px solid #45475a;
            }
            QMenu::item:selected {
                background-color: #3498db;
                color: #ffffff;
            }
            QSplitter::handle {
                background-color: #313244;
            }
            QCheckBox {
                color: #cdd6f4;
            }
            QLineEdit, QComboBox {
                background-color: #313244;
                color: #cdd6f4;
                border: 1px solid #45475a;
                border-radius: 3px;
                padding: 4px;
            }
            QComboBox::drop-down {
                border: none;
            }
            QListWidget {
                background-color: #181825;
                color: #cdd6f4;
                border: 1px solid #45475a;
            }
            QListWidget::item:selected {
                background-color: #3498db;
                color: #ffffff;
            }
        """)

        if self.settings.get('auto_scan_registry_on_launch'):
            self.scanRegistry()

    def createSidebar(self):
        """Create navigation sidebar"""
        sidebar = QWidget()
        sidebar.setMaximumWidth(200)
        sidebar.setMinimumWidth(180)
        sidebar.setStyleSheet("""
            QWidget {
                background-color: #181825;
            }
            QPushButton {
                background-color: transparent;
                color: #cdd6f4;
                text-align: left;
                padding: 12px 15px;
                border: none;
                border-radius: 0;
                font-size: 13px;
            }
            QPushButton:hover {
                background-color: #313244;
            }
            QPushButton:pressed {
                background-color: #45475a;
            }
            QPushButton[selected="true"] {
                background-color: #3498db;
                color: #ffffff;
                border-left: 4px solid #a6e3a1;
            }
        """)
        
        layout = QVBoxLayout()
        layout.setContentsMargins(0, 10, 0, 0)
        layout.setSpacing(0)
        sidebar.setLayout(layout)
        
        # Navigation buttons
        nav_buttons = [
            ('Dashboard', 'dashboard'),
            ('Registry', 'registry'),
            ('Junk Files', 'files'),
            ('Startup', 'startup'),
            ('Privacy', 'privacy'),
            ('Programs', 'uninstaller'),
            ('Repair', 'repair'),
            ('Tools', 'tools'),
            ('Backups', 'backups'),
            ('Settings', 'settings')
        ]
        
        self.nav_buttons = {}
        
        for text, page_id in nav_buttons:
            btn = QPushButton(text)
            btn.setCursor(Qt.PointingHandCursor)
            btn.clicked.connect(lambda checked, p=page_id: self.showPage(p))
            layout.addWidget(btn)
            self.nav_buttons[page_id] = btn
        
        layout.addStretch()
        
        # Version info at bottom
        version_label = QLabel("REGwintool v2.1")
        version_label.setStyleSheet("color: #6c7086; font-size: 10px; padding: 10px;")
        version_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(version_label)
        
        return sidebar
    
    def showPage(self, page_id):
        """Show a specific page"""
        if page_id in self.pages:
            self.content_stack.setCurrentWidget(self.pages[page_id])
            self.current_page_id = page_id

        # Update sidebar button highlight states
        for btn_id, btn in self.nav_buttons.items():
            btn.setProperty('selected', 'true' if btn_id == page_id else 'false')
            btn.style().unpolish(btn)
            btn.style().polish(btn)
    
    def createMenuBar(self):
        """Create menu bar"""
        menubar = self.menuBar()
        menubar.setStyleSheet("""
            QMenuBar {
                background-color: #181825;
                color: #cdd6f4;
                padding: 5px;
            }
            QMenuBar::item {
                background-color: transparent;
                padding: 5px 10px;
            }
            QMenuBar::item:selected {
                background-color: #313244;
                color: #ffffff;
            }
            QMenu {
                background-color: #1e1e2e;
                color: #cdd6f4;
                border: 1px solid #45475a;
            }
            QMenu::item {
                padding: 5px 25px;
                color: #cdd6f4;
            }
            QMenu::item:selected {
                background-color: #3498db;
                color: #ffffff;
            }
        """)
        
        # File menu
        file_menu = menubar.addMenu('&File')
        
        if not self.is_admin:
            admin_action = QAction('Run as Administrator', self)
            admin_action.triggered.connect(self.restartAsAdmin)
            file_menu.addAction(admin_action)
            file_menu.addSeparator()
        
        backup_action = QAction('Create Backup', self)
        backup_action.triggered.connect(self.createFullBackup)
        file_menu.addAction(backup_action)
        
        file_menu.addSeparator()
        
        exit_action = QAction('Exit', self)
        exit_action.setShortcut('Ctrl+Q')
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)
        
        # Tools menu
        tools_menu = menubar.addMenu('&Tools')
        
        quick_scan_action = QAction('Quick Scan', self)
        quick_scan_action.setShortcut('Ctrl+S')
        quick_scan_action.triggered.connect(self.quickScan)
        tools_menu.addAction(quick_scan_action)
        
        ram_action = QAction('RAM Optimizer', self)
        ram_action.triggered.connect(self.optimizeRAM)
        tools_menu.addAction(ram_action)
        
        # View menu
        view_menu = menubar.addMenu('&View')
        
        refresh_action = QAction('Refresh', self)
        refresh_action.setShortcut('F5')
        refresh_action.triggered.connect(self.refreshCurrentPage)
        view_menu.addAction(refresh_action)
        
        # Help menu
        help_menu = menubar.addMenu('&Help')
        
        help_action = QAction('Help', self)
        help_action.setShortcut('F1')
        help_action.triggered.connect(self.showHelp)
        help_menu.addAction(help_action)
        
        about_action = QAction('About', self)
        about_action.triggered.connect(self.showAbout)
        help_menu.addAction(about_action)
    
    def refreshCurrentPage(self):
        """Re-run whichever page is currently visible"""
        page_id = getattr(self, 'current_page_id', 'dashboard')
        refreshers = {
            'registry': self.scanRegistry,
            'files': self.scanJunkFiles,
            'startup': self.loadStartupItems,
            'uninstaller': self.loadInstalledPrograms,
            'backups': self.loadBackupList,
        }
        action = refreshers.get(page_id)
        if action:
            action()
        else:
            self.status_bar.showMessage('Refreshed', 2000)

    def closeEvent(self, event):
        """Persist the current page on exit if 'remember last page' is on"""
        if self.settings.get('remember_last_page'):
            self.settings['last_page'] = getattr(self, 'current_page_id', 'dashboard')
            save_settings(self.settings)
        event.accept()

    def restartAsAdmin(self):
        """Restart program with admin rights"""
        reply = QMessageBox.question(
            self,
            'Restart as Administrator?',
            'Restart REGwintool with Administrator privileges?\n\n'
            'This will enable all features including:\n'
            '• Registry fixes\n'
            '• Startup management\n'
            '• System-wide cleaning',
            QMessageBox.Yes | QMessageBox.No
        )
        if reply == QMessageBox.Yes:
            if run_as_admin():
                self.close()
            else:
                QMessageBox.warning(self, 'Elevation Failed',
                    'Could not restart as Administrator.\n'
                    'Try right-clicking the script and selecting "Run as administrator".')
    
    # ==================== PAGE CREATORS ====================
    
    def createDashboardPage(self):
        """Create dashboard page"""
        page = QWidget()
        layout = QVBoxLayout()
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(15)
        page.setLayout(layout)
        
        # Page title
        title = QLabel("Dashboard")
        title.setFont(QFont("Arial", 18, QFont.Bold))
        title.setStyleSheet("color: #89b4fa;")
        layout.addWidget(title)
        
        # Admin status card
        admin_card = QGroupBox("System Status")
        admin_layout = QVBoxLayout()
        
        if self.is_admin:
            admin_html = """
            <div style='padding: 10px;'>
                <h3 style='color: #a6e3a1; margin: 0;'>Administrator Mode Active</h3>
                <p style='margin: 10px 0 5px 0;'><b>All features enabled:</b></p>
                <ul style='margin: 5px 0; padding-left: 20px;'>
                    <li>Registry scanning and fixes</li>
                    <li>Startup program management</li>
                    <li>System-wide file cleaning</li>
                    <li>Full system access</li>
                </ul>
            </div>
            """
        else:
            admin_html = """
            <div style='padding: 10px;'>
                <h3 style='color: #fab387; margin: 0;'>User Mode</h3>
                <p style='margin: 10px 0 5px 0;'><b>Available features:</b></p>
                <ul style='margin: 5px 0; padding-left: 20px;'>
                    <li>Registry scanning (read-only)</li>
                    <li>User file cleaning</li>
                    <li>Backups and privacy tools</li>
                </ul>
                <p style='margin: 10px 0 5px 0;'><b>Requires Administrator:</b></p>
                <ul style='margin: 5px 0; padding-left: 20px;'>
                    <li>Registry fixes</li>
                    <li>Startup management</li>
                    <li>System file operations</li>
                </ul>
            </div>
            """
        
        admin_info = QLabel(admin_html)
        admin_info.setWordWrap(True)
        admin_layout.addWidget(admin_info)
        admin_card.setLayout(admin_layout)
        layout.addWidget(admin_card)
        
        # System info card
        info_card = QGroupBox("System Information")
        info_layout = QVBoxLayout()
        
        try:
            import platform
            sys_info = f"""
            <table style='width: 100%;'>
                <tr><td><b>OS:</b></td><td>{platform.system()} {platform.release()}</td></tr>
                <tr><td><b>Computer:</b></td><td>{platform.node()}</td></tr>
                <tr><td><b>Processor:</b></td><td>{platform.processor()}</td></tr>
                <tr><td><b>Python:</b></td><td>{platform.python_version()}</td></tr>
                <tr><td><b>Backup Location:</b></td><td>{self.backup_dir}</td></tr>
            </table>
            """
            
            try:
                import psutil
                ram = psutil.virtual_memory()
                disk = psutil.disk_usage('C:')
                sys_info += f"""
                <table style='width: 100%; margin-top: 10px;'>
                    <tr><td><b>RAM:</b></td><td>{ram.used/1024**3:.1f} GB / {ram.total/1024**3:.1f} GB ({ram.percent}%)</td></tr>
                    <tr><td><b>Disk C:</b></td><td>{disk.used/1024**3:.1f} GB / {disk.total/1024**3:.1f} GB ({disk.percent}%)</td></tr>
                    <tr><td><b>CPU Cores:</b></td><td>{psutil.cpu_count()}</td></tr>
                </table>
                """
            except ImportError:
                sys_info += "<p style='color: #7f8c8d; margin-top: 10px;'><i>Install psutil for detailed stats: pip install psutil</i></p>"
        except Exception:
            sys_info = "<p style='color: #f38ba8;'>Unable to retrieve system information</p>"
        
        info_label = QLabel(sys_info)
        info_label.setWordWrap(True)
        info_layout.addWidget(info_label)
        info_card.setLayout(info_layout)
        layout.addWidget(info_card)
        
        # Quick actions card
        actions_card = QGroupBox("Quick Actions")
        actions_layout = QVBoxLayout()
        
        # Row 1
        row1 = QHBoxLayout()
        
        scan_btn = QPushButton("Scan System")
        scan_btn.setMinimumHeight(60)
        scan_btn.clicked.connect(self.quickScan)
        row1.addWidget(scan_btn)
        
        backup_btn = QPushButton("Create Backup")
        backup_btn.setMinimumHeight(60)
        backup_btn.clicked.connect(self.createFullBackup)
        row1.addWidget(backup_btn)
        
        actions_layout.addLayout(row1)
        
        # Row 2
        row2 = QHBoxLayout()
        
        clean_btn = QPushButton("Clean Junk Files")
        clean_btn.setMinimumHeight(60)
        clean_btn.clicked.connect(lambda: self.showPage('files'))
        row2.addWidget(clean_btn)
        
        privacy_btn = QPushButton("Privacy Cleanup")
        privacy_btn.setMinimumHeight(60)
        privacy_btn.clicked.connect(lambda: self.showPage('privacy'))
        row2.addWidget(privacy_btn)
        
        actions_layout.addLayout(row2)
        
        actions_card.setLayout(actions_layout)
        layout.addWidget(actions_card)
        
        # Activity log
        log_card = QGroupBox("Activity Log")
        log_layout = QVBoxLayout()
        
        self.activity_log = QTextEdit()
        self.activity_log.setReadOnly(True)
        self.activity_log.setMaximumHeight(120)
        
        mode = "Admin" if self.is_admin else "User"
        self.activity_log.setText(f"[{datetime.now().strftime('%H:%M:%S')}] REGwintool started in {mode} mode")
        
        log_layout.addWidget(self.activity_log)
        
        clear_log_btn = QPushButton("Clear Log")
        clear_log_btn.setFixedWidth(100)
        clear_log_btn.clicked.connect(lambda: self.activity_log.clear())
        log_layout.addWidget(clear_log_btn, alignment=Qt.AlignRight)
        
        log_card.setLayout(log_layout)
        layout.addWidget(log_card)
        
        layout.addStretch()
        
        return page
    
    def createRegistryPage(self):
        """Create registry cleaner page"""
        page = QWidget()
        layout = QVBoxLayout()
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(15)
        page.setLayout(layout)
        
        # Title
        title = QLabel("Registry Cleaner")
        title.setFont(QFont("Arial", 18, QFont.Bold))
        layout.addWidget(title)
        
        # Status banner
        if not self.is_admin:
            warning = QLabel("USER MODE: Can scan only. Fixes require Administrator rights.")
            warning.setStyleSheet("""
                background-color: #fab387;
                color: #1e1e2e;
                padding: 10px;
                border-radius: 4px;
                font-weight: bold;
            """)
            layout.addWidget(warning)
        else:
            info = QLabel("ADMIN MODE: Automatic backup created before fixes")
            info.setStyleSheet("""
                background-color: #a6e3a1;
                color: #1e1e2e;
                padding: 10px;
                border-radius: 4px;
                font-weight: bold;
            """)
            layout.addWidget(info)
        
        # Results table
        self.reg_table = QTableWidget()
        self.reg_table.setColumnCount(5)
        self.reg_table.setHorizontalHeaderLabels(['Select', 'Type', 'Name', 'Problem', 'Location'])
        self.reg_table.horizontalHeader().setStretchLastSection(True)
        self.reg_table.verticalHeader().setVisible(False)
        self.reg_table.setAlternatingRowColors(True)
        self.reg_table.setColumnWidth(0, 60)
        self.reg_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.reg_table.setMaximumHeight(380)
        layout.addWidget(self.reg_table)
        
        # Summary
        self.reg_summary = QLabel("No scan performed yet")
        self.reg_summary.setFont(QFont("Arial", 11, QFont.Bold))
        self.reg_summary.setStyleSheet("color: #a6e3a1; padding: 5px;")
        layout.addWidget(self.reg_summary)
        
        # Buttons
        btn_layout = QHBoxLayout()
        
        scan_btn = QPushButton("Scan Registry")
        scan_btn.setMinimumHeight(40)
        scan_btn.clicked.connect(self.scanRegistry)
        btn_layout.addWidget(scan_btn)
        
        select_all_btn = QPushButton("Select All")
        select_all_btn.setMinimumHeight(40)
        select_all_btn.clicked.connect(self.selectAllRegistry)
        btn_layout.addWidget(select_all_btn)
        
        deselect_all_btn = QPushButton("Deselect All")
        deselect_all_btn.setMinimumHeight(40)
        deselect_all_btn.clicked.connect(self.deselectAllRegistry)
        btn_layout.addWidget(deselect_all_btn)
        
        self.fix_reg_btn = QPushButton("Fix Selected" if self.is_admin else "Fix (Needs Admin)")
        self.fix_reg_btn.setMinimumHeight(40)
        self.fix_reg_btn.setEnabled(False)
        self.fix_reg_btn.clicked.connect(self.fixRegistry)
        btn_layout.addWidget(self.fix_reg_btn)

        delete_reg_btn = QPushButton("Delete Selected")
        delete_reg_btn.setMinimumHeight(40)
        delete_reg_btn.setStyleSheet("QPushButton { background-color: #f38ba8; color: #1e1e2e; } QPushButton:hover { background-color: #eb6f92; }")
        delete_reg_btn.clicked.connect(self.deleteRegistrySelected)
        btn_layout.addWidget(delete_reg_btn)

        layout.addLayout(btn_layout)

        return page

    def deleteRegistrySelected(self):
        """Delete selected registry entries permanently"""
        selected_rows = []
        for row in range(self.reg_table.rowCount()):
            checkbox_widget = self.reg_table.cellWidget(row, 0)
            if checkbox_widget:
                checkbox = checkbox_widget.findChild(QCheckBox)
                if checkbox and checkbox.isChecked():
                    selected_rows.append(row)

        if not selected_rows:
            QMessageBox.warning(self, 'No Selection', 'Please select items to delete using the checkboxes.')
            return

        reply = QMessageBox.warning(
            self, 'Confirm Delete',
            f'Permanently delete {len(selected_rows)} registry entry/entries?\n\nThis cannot be undone.',
            QMessageBox.Yes | QMessageBox.No
        )
        if reply != QMessageBox.Yes:
            return

        deleted = 0
        errors = []
        for row in sorted(selected_rows, reverse=True):
            issue = self.scan_results[row]
            try:
                # Use the hive and fix_type stored by the scanner — same as fixRegistryIssue()
                flags = winreg.KEY_ALL_ACCESS | winreg.KEY_WOW64_64KEY
                if issue.get('fix_type') == 'delete_value':
                    with winreg.OpenKey(issue['hive'], issue['location'], 0, flags) as key:
                        winreg.DeleteValue(key, issue.get('value_name', issue['name']))
                elif issue.get('fix_type') == 'delete_key':
                    if not recursive_delete_key(issue['hive'], issue['location']):
                        raise PermissionError('Key is protected or in use')
                else:
                    # Fallback: try delete_value first, then delete_key
                    try:
                        with winreg.OpenKey(issue['hive'], issue['location'], 0, flags) as key:
                            winreg.DeleteValue(key, issue['name'])
                    except FileNotFoundError:
                        if not recursive_delete_key(issue['hive'], issue['location']):
                            raise PermissionError('Key is protected or in use')

                self.reg_table.removeRow(row)
                self.scan_results.pop(row)
                deleted += 1
            except PermissionError:
                errors.append(f'{issue["name"]}: Access denied — run as Administrator')
            except Exception as e:
                errors.append(f'{issue["name"]}: {e}')

        self.status_bar.showMessage(f'Deleted {deleted} entries')
        self.logActivity(f'Registry: deleted {deleted} entries')
        if errors:
            QMessageBox.warning(self, 'Some Deletions Failed',
                '\n'.join(errors[:10]))

    def createFilesPage(self):
        """Create junk files page"""
        page = QWidget()
        layout = QVBoxLayout()
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(15)
        page.setLayout(layout)
        
        # Title
        title = QLabel("Junk File Cleaner")
        title.setFont(QFont("Arial", 18, QFont.Bold))
        layout.addWidget(title)
        
        # Info
        info = QLabel("Can clean user temp files. System files may need Admin rights.")
        info.setStyleSheet("""
            background-color: #3498db;
            color: white;
            padding: 10px;
            border-radius: 4px;
            font-weight: bold;
        """)
        layout.addWidget(info)
        
        # Results table
        self.files_table = QTableWidget()
        self.files_table.setColumnCount(5)
        self.files_table.setHorizontalHeaderLabels(['Select', 'Type', 'Location', 'Size', 'Items'])
        self.files_table.horizontalHeader().setStretchLastSection(True)
        self.files_table.verticalHeader().setVisible(False)
        self.files_table.setAlternatingRowColors(True)
        self.files_table.setColumnWidth(0, 60)
        self.files_table.setMaximumHeight(380)
        layout.addWidget(self.files_table)
        
        # Size summary
        self.size_label = QLabel("Total: 0 MB")
        self.size_label.setFont(QFont("Arial", 12, QFont.Bold))
        self.size_label.setStyleSheet("color: #a6e3a1; padding: 5px;")
        layout.addWidget(self.size_label)
        
        # Buttons
        btn_layout = QHBoxLayout()
        
        scan_btn = QPushButton("Scan for Junk")
        scan_btn.setMinimumHeight(40)
        scan_btn.clicked.connect(self.scanJunkFiles)
        btn_layout.addWidget(scan_btn)
        
        select_all_btn = QPushButton("Select All")
        select_all_btn.setMinimumHeight(40)
        select_all_btn.clicked.connect(self.selectAllFiles)
        btn_layout.addWidget(select_all_btn)
        
        deselect_all_btn = QPushButton("Deselect All")
        deselect_all_btn.setMinimumHeight(40)
        deselect_all_btn.clicked.connect(self.deselectAllFiles)
        btn_layout.addWidget(deselect_all_btn)
        
        self.clean_files_btn = QPushButton("Delete Selected")
        self.clean_files_btn.setMinimumHeight(40)
        self.clean_files_btn.setEnabled(False)
        self.clean_files_btn.clicked.connect(self.cleanFiles)
        self.clean_files_btn.setStyleSheet("QPushButton { background-color: #f38ba8; color: #1e1e2e; } QPushButton:hover { background-color: #eb6f92; }")
        btn_layout.addWidget(self.clean_files_btn)
        
        layout.addLayout(btn_layout)
        
        return page
    
    def createStartupPage(self):
        """Create startup manager page"""
        page = QWidget()
        layout = QVBoxLayout()
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(15)
        page.setLayout(layout)
        
        # Title
        title = QLabel("Startup Manager")
        title.setFont(QFont("Arial", 18, QFont.Bold))
        layout.addWidget(title)
        
        # Status banner
        if not self.is_admin:
            warning = QLabel("USER MODE: View only. Changes require Administrator rights.")
            warning.setStyleSheet("""
                background-color: #fab387;
                color: #1e1e2e;
                padding: 10px;
                border-radius: 4px;
                font-weight: bold;
            """)
            layout.addWidget(warning)
        else:
            info = QLabel("ADMIN MODE: Can enable/disable/delete startup items")
            info.setStyleSheet("""
                background-color: #a6e3a1;
                color: #1e1e2e;
                padding: 10px;
                border-radius: 4px;
                font-weight: bold;
            """)
            layout.addWidget(info)
        
        # Table
        self.startup_table = QTableWidget()
        self.startup_table.setColumnCount(5)
        self.startup_table.setHorizontalHeaderLabels(['Program', 'Publisher', 'Status', 'Impact', 'Location'])
        self.startup_table.horizontalHeader().setStretchLastSection(True)
        self.startup_table.verticalHeader().setVisible(False)
        self.startup_table.setAlternatingRowColors(True)
        self.startup_table.setMaximumHeight(380)
        layout.addWidget(self.startup_table)
        
        # Buttons
        btn_layout = QHBoxLayout()
        
        refresh_btn = QPushButton("Refresh List")
        refresh_btn.setMinimumHeight(40)
        refresh_btn.clicked.connect(self.loadStartupItems)
        btn_layout.addWidget(refresh_btn)
        
        disable_btn = QPushButton("Disable" if self.is_admin else "Disable (Locked)")
        disable_btn.setMinimumHeight(40)
        disable_btn.clicked.connect(self.disableStartup)
        if not self.is_admin:
            disable_btn.setEnabled(False)
        btn_layout.addWidget(disable_btn)
        
        enable_btn = QPushButton("Enable" if self.is_admin else "Enable (Locked)")
        enable_btn.setMinimumHeight(40)
        enable_btn.clicked.connect(self.enableStartup)
        if not self.is_admin:
            enable_btn.setEnabled(False)
        btn_layout.addWidget(enable_btn)
        
        delete_btn = QPushButton("Delete Entry" if self.is_admin else "Delete (Locked)")
        delete_btn.setMinimumHeight(40)
        delete_btn.clicked.connect(self.deleteStartup)
        if not self.is_admin:
            delete_btn.setEnabled(False)
        btn_layout.addWidget(delete_btn)
        
        uninstall_btn = QPushButton("Uninstall Program")
        uninstall_btn.setMinimumHeight(40)
        uninstall_btn.clicked.connect(self.uninstallStartupProgram)
        btn_layout.addWidget(uninstall_btn)
        
        layout.addLayout(btn_layout)
        
        return page
    
    def createPrivacyPage(self):
        """Create privacy tools page"""
        page = QWidget()
        layout = QVBoxLayout()
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(15)
        page.setLayout(layout)
        
        # Title
        title = QLabel("Privacy Tools")
        title.setFont(QFont("Arial", 18, QFont.Bold))
        layout.addWidget(title)
        
        # Create scroll area for privacy options
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        
        scroll_content = QWidget()
        scroll_layout = QVBoxLayout()
        scroll_content.setLayout(scroll_layout)
        
        # Browser privacy
        browser_card = QGroupBox("Browser Privacy")
        browser_layout = QVBoxLayout()
        
        browsers = [
            ("Clear Chrome Data", lambda: self.clearBrowserData('Chrome')),
            ("Clear Firefox Data", lambda: self.clearBrowserData('Firefox')),
            ("Clear Edge Data", lambda: self.clearBrowserData('Edge')),
            ("Clear All Browsers", self.clearAllBrowsers)
        ]
        
        for text, func in browsers:
            btn = QPushButton(text)
            btn.setMinimumHeight(40)
            btn.clicked.connect(func)
            browser_layout.addWidget(btn)
        
        browser_card.setLayout(browser_layout)
        scroll_layout.addWidget(browser_card)
        
        # System privacy
        system_card = QGroupBox("System Privacy")
        system_layout = QVBoxLayout()
        
        system_actions = [
            ("Clear Temp Files", self.clearTempFiles),
            ("Clear Recent Files", self.clearRecentFiles),
            ("Clear Clipboard", self.clearClipboard),
            ("Flush DNS Cache", self.flushDNS),
            ("Clear Prefetch", self.clearPrefetch)
        ]
        
        for text, func in system_actions:
            btn = QPushButton(text)
            btn.setMinimumHeight(40)
            btn.clicked.connect(func)
            system_layout.addWidget(btn)
        
        system_card.setLayout(system_layout)
        scroll_layout.addWidget(system_card)
        
        scroll_layout.addStretch()
        
        scroll.setWidget(scroll_content)
        layout.addWidget(scroll)
        
        # Privacy log
        log_card = QGroupBox("Privacy Activity Log")
        log_layout = QVBoxLayout()
        
        self.privacy_log = QTextEdit()
        self.privacy_log.setReadOnly(True)
        self.privacy_log.setMaximumHeight(100)
        self.privacy_log.setText("No privacy actions taken yet.")
        log_layout.addWidget(self.privacy_log)
        
        log_card.setLayout(log_layout)
        layout.addWidget(log_card)
        
        return page
    
    def createUninstallerPage(self):
        """Create uninstaller page"""
        page = QWidget()
        layout = QVBoxLayout()
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(15)
        page.setLayout(layout)
        
        # Title
        title = QLabel("Installed Programs")
        title.setFont(QFont("Arial", 18, QFont.Bold))
        layout.addWidget(title)
        
        # Info
        info = QLabel("Check one or more programs, then click 'Uninstall Selected' to remove them.")
        info.setStyleSheet("""
            background-color: #3498db;
            color: white;
            padding: 10px;
            border-radius: 4px;
        """)
        layout.addWidget(info)

        # Table
        self.uninstall_table = QTableWidget()
        self.uninstall_table.setColumnCount(5)
        self.uninstall_table.setHorizontalHeaderLabels(['Select', 'Program', 'Publisher', 'Version', 'Install Date'])
        self.uninstall_table.horizontalHeader().setStretchLastSection(True)
        self.uninstall_table.verticalHeader().setVisible(False)
        self.uninstall_table.setAlternatingRowColors(True)
        self.uninstall_table.setColumnWidth(0, 60)
        self.uninstall_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.uninstall_table.setMaximumHeight(380)
        layout.addWidget(self.uninstall_table)

        # Buttons
        btn_layout = QHBoxLayout()

        refresh_btn = QPushButton("Refresh List")
        refresh_btn.setMinimumHeight(40)
        refresh_btn.clicked.connect(self.loadInstalledPrograms)
        btn_layout.addWidget(refresh_btn)

        select_all_btn = QPushButton("Select All")
        select_all_btn.setMinimumHeight(40)
        select_all_btn.clicked.connect(self.selectAllPrograms)
        btn_layout.addWidget(select_all_btn)

        deselect_all_btn = QPushButton("Deselect All")
        deselect_all_btn.setMinimumHeight(40)
        deselect_all_btn.clicked.connect(self.deselectAllPrograms)
        btn_layout.addWidget(deselect_all_btn)

        settings_btn = QPushButton("Open Windows Settings")
        settings_btn.setMinimumHeight(40)
        settings_btn.clicked.connect(self.openWindowsSettings)
        btn_layout.addWidget(settings_btn)

        uninstall_btn = QPushButton("Uninstall Selected")
        uninstall_btn.setMinimumHeight(40)
        uninstall_btn.setStyleSheet("QPushButton { background-color: #f38ba8; color: #1e1e2e; } QPushButton:hover { background-color: #eb6f92; }")
        uninstall_btn.clicked.connect(self.uninstallSelectedPrograms)
        btn_layout.addWidget(uninstall_btn)

        layout.addLayout(btn_layout)

        # ---- Install Software (winget) ----
        install_title = QLabel("Install Software")
        install_title.setFont(QFont("Arial", 12, QFont.Bold))
        install_title.setStyleSheet("color: #a6adc8; margin-top: 10px;")
        layout.addWidget(install_title)

        install_card = QGroupBox("winget Package Manager")
        install_layout = QVBoxLayout()

        search_row = QHBoxLayout()
        self.winget_query = QLineEdit()
        self.winget_query.setPlaceholderText("Package name or ID, e.g. 7zip or Mozilla.Firefox")
        search_row.addWidget(self.winget_query, 1)

        search_btn = QPushButton("Search")
        search_btn.clicked.connect(self.wingetSearch)
        search_row.addWidget(search_btn)

        install_btn = QPushButton("Install")
        install_btn.setStyleSheet("QPushButton { background-color: #a6e3a1; color: #1e1e2e; } QPushButton:hover { background-color: #94d888; }")
        install_btn.clicked.connect(self.wingetInstall)
        search_row.addWidget(install_btn)

        upgrade_all_btn = QPushButton("Upgrade All Installed Apps")
        upgrade_all_btn.setToolTip("Runs 'winget upgrade --all' - updates everything winget manages in one pass.")
        upgrade_all_btn.clicked.connect(self.wingetUpgradeAll)
        search_row.addWidget(upgrade_all_btn)

        install_layout.addLayout(search_row)

        self.winget_buttons = [search_btn, install_btn, upgrade_all_btn]

        self.winget_output = QTextEdit()
        self.winget_output.setReadOnly(True)
        self.winget_output.setMaximumHeight(220)
        self.winget_output.setText("Search results and install progress appear here.")
        install_layout.addWidget(self.winget_output)

        install_card.setLayout(install_layout)
        layout.addWidget(install_card)

        return page

    def createRepairPage(self):
        """Create the Windows Update / Defender / system repair page"""
        page = QWidget()
        layout = QVBoxLayout()
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(15)
        page.setLayout(layout)

        title = QLabel("Repair Tools")
        title.setFont(QFont("Arial", 18, QFont.Bold))
        layout.addWidget(title)

        if not self.is_admin:
            warning = QLabel("USER MODE: Most repair operations require Administrator rights.")
            warning.setStyleSheet("""
                background-color: #fab387;
                color: #1e1e2e;
                padding: 10px;
                border-radius: 4px;
                font-weight: bold;
            """)
            layout.addWidget(warning)

        # Windows Update
        update_card = QGroupBox("Windows Update")
        update_layout = QHBoxLayout()

        quick_reset_btn = QPushButton("Quick Reset")
        quick_reset_btn.setMinimumHeight(40)
        quick_reset_btn.setToolTip("Stops update services, renames the SoftwareDistribution/catroot2 cache folders, restarts services.")
        quick_reset_btn.clicked.connect(self.windowsUpdateQuickReset)
        update_layout.addWidget(quick_reset_btn)

        complete_repair_btn = QPushButton("Complete Repair")
        complete_repair_btn.setMinimumHeight(40)
        complete_repair_btn.setToolTip("Full repair: clears cache, fixes registry entries, re-registers update DLLs, resets policies. Takes 5-10 minutes.")
        complete_repair_btn.setStyleSheet("QPushButton { background-color: #fab387; color: #1e1e2e; } QPushButton:hover { background-color: #f5a26a; }")
        complete_repair_btn.clicked.connect(self.windowsUpdateCompleteRepair)
        update_layout.addWidget(complete_repair_btn)

        reregister_btn = QPushButton("Re-register Update DLLs")
        reregister_btn.setMinimumHeight(40)
        reregister_btn.clicked.connect(self.reregisterUpdateDLLs)
        update_layout.addWidget(reregister_btn)

        update_card.setLayout(update_layout)
        layout.addWidget(update_card)

        # Windows Defender
        defender_card = QGroupBox("Windows Defender")
        defender_layout = QHBoxLayout()

        defender_reenable_btn = QPushButton("Re-enable Defender")
        defender_reenable_btn.setMinimumHeight(40)
        defender_reenable_btn.clicked.connect(self.defenderReenable)
        defender_layout.addWidget(defender_reenable_btn)

        defender_reset_btn = QPushButton("Reset To Defaults")
        defender_reset_btn.setMinimumHeight(40)
        defender_reset_btn.clicked.connect(self.defenderResetDefaults)
        defender_layout.addWidget(defender_reset_btn)

        defender_repair_btn = QPushButton("Complete Defender Repair")
        defender_repair_btn.setMinimumHeight(40)
        defender_repair_btn.setToolTip("Re-enables Defender, resets all protection settings, updates definitions, runs a quick scan.")
        defender_repair_btn.setStyleSheet("QPushButton { background-color: #fab387; color: #1e1e2e; } QPushButton:hover { background-color: #f5a26a; }")
        defender_repair_btn.clicked.connect(self.defenderCompleteRepair)
        defender_layout.addWidget(defender_repair_btn)

        defender_card.setLayout(defender_layout)
        layout.addWidget(defender_card)

        # System Repair
        system_card = QGroupBox("System Repair")
        system_layout = QHBoxLayout()

        health_check_btn = QPushButton("Quick Health Check")
        health_check_btn.setMinimumHeight(40)
        health_check_btn.setToolTip("Safe, read-only: disk health, memory, system file check, network test, recent critical errors.")
        health_check_btn.clicked.connect(self.quickHealthCheck)
        system_layout.addWidget(health_check_btn)

        auto_repair_btn = QPushButton("SFC + DISM Repair")
        auto_repair_btn.setMinimumHeight(40)
        auto_repair_btn.setToolTip("Runs 'sfc /scannow' and 'DISM /RestoreHealth' to repair corrupted system files. Takes 15-30 minutes.")
        auto_repair_btn.setStyleSheet("QPushButton { background-color: #fab387; color: #1e1e2e; } QPushButton:hover { background-color: #f5a26a; }")
        auto_repair_btn.clicked.connect(self.autoRepairSFCDISM)
        system_layout.addWidget(auto_repair_btn)

        restore_point_btn = QPushButton("Create Restore Point")
        restore_point_btn.setMinimumHeight(40)
        restore_point_btn.clicked.connect(self.createSystemRestorePoint)
        system_layout.addWidget(restore_point_btn)

        check_disk_btn = QPushButton("Check Disk (chkdsk)")
        check_disk_btn.setMinimumHeight(40)
        check_disk_btn.setToolTip("Schedules a disk error check for next restart.")
        check_disk_btn.clicked.connect(self.checkDiskScan)
        system_layout.addWidget(check_disk_btn)

        system_card.setLayout(system_layout)
        layout.addWidget(system_card)

        self.repair_buttons = [
            quick_reset_btn, complete_repair_btn, reregister_btn,
            defender_reenable_btn, defender_reset_btn, defender_repair_btn,
            health_check_btn, auto_repair_btn, restore_point_btn, check_disk_btn,
        ]

        # Log
        self.repair_log = QTextEdit()
        self.repair_log.setReadOnly(True)
        self.repair_log.setText("Ready. Repair operations stream their output here.")
        layout.addWidget(self.repair_log)

        return page

    def createToolsPage(self):
        """Create system tools page"""
        page = QWidget()
        layout = QVBoxLayout()
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(15)
        page.setLayout(layout)
        
        # Title
        title = QLabel("System Tools")
        title.setFont(QFont("Arial", 18, QFont.Bold))
        layout.addWidget(title)
        
        # Windows tools
        windows_card = QGroupBox("Windows Built-in Tools")
        windows_layout = QVBoxLayout()
        
        row1 = QHBoxLayout()
        tools = [
            ("Disk Cleanup", self.openDiskCleanup),
            ("Defragment", self.openDefrag)
        ]
        for text, func in tools:
            btn = QPushButton(text)
            btn.setMinimumHeight(60)
            btn.clicked.connect(func)
            row1.addWidget(btn)
        windows_layout.addLayout(row1)
        
        row2 = QHBoxLayout()
        tools2 = [
            ("Task Manager", self.openTaskManager),
            ("Services", self.openServices)
        ]
        for text, func in tools2:
            btn = QPushButton(text)
            btn.setMinimumHeight(60)
            btn.clicked.connect(func)
            row2.addWidget(btn)
        windows_layout.addLayout(row2)
        
        row3 = QHBoxLayout()
        tools3 = [
            ("Registry Editor", self.openRegedit),
            ("System Info", self.openSystemInfo)
        ]
        for text, func in tools3:
            btn = QPushButton(text)
            btn.setMinimumHeight(60)
            btn.clicked.connect(func)
            row3.addWidget(btn)
        windows_layout.addLayout(row3)
        
        windows_card.setLayout(windows_layout)
        layout.addWidget(windows_card)
        
        # Custom tools
        custom_card = QGroupBox("REGwintool Utilities")
        custom_layout = QVBoxLayout()
        
        ram_btn = QPushButton("RAM Optimizer")
        ram_btn.setMinimumHeight(50)
        ram_btn.clicked.connect(self.optimizeRAM)
        custom_layout.addWidget(ram_btn)
        
        disk_btn = QPushButton("Disk Space Analyzer")
        disk_btn.setMinimumHeight(50)
        disk_btn.clicked.connect(self.analyzeDisk)
        custom_layout.addWidget(disk_btn)
        
        custom_card.setLayout(custom_layout)
        layout.addWidget(custom_card)

        # Windows Tweaks
        tweaks_card = QGroupBox("Windows Tweaks")
        tweaks_layout = QVBoxLayout()

        tweaks_info = QLabel("Check the tweaks you want, then Apply. Each is a single registry change - reversible "
                              "by setting the underlying Windows option back through Settings.")
        tweaks_info.setWordWrap(True)
        tweaks_layout.addWidget(tweaks_info)

        self.tweaks_table = QTableWidget()
        self.tweaks_table.setColumnCount(2)
        self.tweaks_table.setHorizontalHeaderLabels(['Select', 'Tweak'])
        self.tweaks_table.horizontalHeader().setStretchLastSection(True)
        self.tweaks_table.verticalHeader().setVisible(False)
        self.tweaks_table.setAlternatingRowColors(True)
        self.tweaks_table.setColumnWidth(0, 60)
        self.tweaks_table.setMaximumHeight(300)
        self.tweaks_table.setRowCount(len(WINDOWS_TWEAKS))
        for row, tweak in enumerate(WINDOWS_TWEAKS):
            checkbox = QCheckBox()
            checkbox_widget = QWidget()
            checkbox_layout = QHBoxLayout(checkbox_widget)
            checkbox_layout.addWidget(checkbox)
            checkbox_layout.setAlignment(Qt.AlignCenter)
            checkbox_layout.setContentsMargins(0, 0, 0, 0)
            self.tweaks_table.setCellWidget(row, 0, checkbox_widget)
            self.tweaks_table.setItem(row, 1, QTableWidgetItem(tweak['name']))
        tweaks_layout.addWidget(self.tweaks_table)

        apply_tweaks_btn = QPushButton("Apply Selected Tweaks")
        apply_tweaks_btn.setMinimumHeight(40)
        apply_tweaks_btn.setStyleSheet("QPushButton { background-color: #a6e3a1; color: #1e1e2e; } QPushButton:hover { background-color: #94d888; }")
        apply_tweaks_btn.clicked.connect(self.applySelectedTweaks)
        tweaks_layout.addWidget(apply_tweaks_btn)

        tweaks_card.setLayout(tweaks_layout)
        layout.addWidget(tweaks_card)

        layout.addStretch()

        return page

    def createBackupsPage(self):
        """Create backups manager page"""
        page = QWidget()
        layout = QVBoxLayout()
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(15)
        page.setLayout(layout)
        
        # Title
        title = QLabel("Backup Manager")
        title.setFont(QFont("Arial", 18, QFont.Bold))
        layout.addWidget(title)

        sub_title = QLabel("Settings & Registry Snapshots")
        sub_title.setFont(QFont("Arial", 12, QFont.Bold))
        sub_title.setStyleSheet("color: #a6adc8;")
        layout.addWidget(sub_title)

        # Backup info
        info_card = QGroupBox("Backup Location")
        info_layout = QHBoxLayout()
        
        info_layout.addWidget(QLabel("Location:"))
        
        self.backup_location_label = QLabel(self.backup_dir)
        self.backup_location_label.setStyleSheet("color: #3498db; font-weight: bold;")
        self.backup_location_label.setWordWrap(True)
        info_layout.addWidget(self.backup_location_label, 1)
        
        open_btn = QPushButton("Open Folder")
        open_btn.clicked.connect(lambda: os.startfile(self.backup_dir) if os.path.exists(self.backup_dir) else None)
        info_layout.addWidget(open_btn)
        
        info_card.setLayout(info_layout)
        layout.addWidget(info_card)
        
        # Backups table
        self.backups_table = QTableWidget()
        self.backups_table.setColumnCount(5)
        self.backups_table.setHorizontalHeaderLabels(['Filename', 'Date', 'Type', 'Size', 'Items'])
        self.backups_table.horizontalHeader().setStretchLastSection(True)
        self.backups_table.verticalHeader().setVisible(False)
        self.backups_table.setAlternatingRowColors(True)
        self.backups_table.setMaximumHeight(380)
        layout.addWidget(self.backups_table)
        
        # Buttons
        btn_layout = QHBoxLayout()
        
        refresh_btn = QPushButton("Refresh")
        refresh_btn.setMinimumHeight(40)
        refresh_btn.clicked.connect(self.loadBackupList)
        btn_layout.addWidget(refresh_btn)
        
        create_btn = QPushButton("Create Backup")
        create_btn.setMinimumHeight(40)
        create_btn.clicked.connect(self.createFullBackup)
        btn_layout.addWidget(create_btn)
        
        view_btn = QPushButton("View")
        view_btn.setMinimumHeight(40)
        view_btn.clicked.connect(self.viewSelectedBackup)
        btn_layout.addWidget(view_btn)
        
        delete_btn = QPushButton("Delete")
        delete_btn.setMinimumHeight(40)
        delete_btn.clicked.connect(self.deleteSelectedBackup)
        delete_btn.setStyleSheet("QPushButton { background-color: #f38ba8; color: #1e1e2e; } QPushButton:hover { background-color: #eb6f92; }")
        btn_layout.addWidget(delete_btn)
        
        layout.addLayout(btn_layout)

        # Load backups
        self.loadBackupList()

        # ---- File Backup (real files, mirrored via robocopy) ----
        file_title = QLabel("File Backup (Mirror)")
        file_title.setFont(QFont("Arial", 12, QFont.Bold))
        file_title.setStyleSheet("color: #a6adc8; margin-top: 10px;")
        layout.addWidget(file_title)

        file_info = QLabel(
            "Backs up actual files and folders, not just settings. Each destination becomes an "
            "EXACT copy of its source - files deleted from the source are also removed from the "
            "backup next time it runs."
        )
        file_info.setWordWrap(True)
        file_info.setStyleSheet("""
            background-color: #3498db;
            color: white;
            padding: 10px;
            border-radius: 4px;
        """)
        layout.addWidget(file_info)

        self.file_backup_table = QTableWidget()
        self.file_backup_table.setColumnCount(3)
        self.file_backup_table.setHorizontalHeaderLabels(['Source', 'Backup Destination', 'Last Run'])
        self.file_backup_table.horizontalHeader().setStretchLastSection(True)
        self.file_backup_table.verticalHeader().setVisible(False)
        self.file_backup_table.setAlternatingRowColors(True)
        self.file_backup_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.file_backup_table.setMaximumHeight(200)
        layout.addWidget(self.file_backup_table)

        file_btn_layout = QHBoxLayout()

        add_job_btn = QPushButton("Add Folder...")
        add_job_btn.setMinimumHeight(40)
        add_job_btn.clicked.connect(self.addFileBackupJob)
        file_btn_layout.addWidget(add_job_btn)

        quick_add_btn = QPushButton("Quick-Add Common Folders")
        quick_add_btn.setMinimumHeight(40)
        quick_add_btn.setToolTip("Adds Desktop, Documents, and Pictures as backup jobs in one click.")
        quick_add_btn.clicked.connect(self.quickAddCommonFolders)
        file_btn_layout.addWidget(quick_add_btn)

        remove_job_btn = QPushButton("Remove Selected")
        remove_job_btn.setMinimumHeight(40)
        remove_job_btn.clicked.connect(self.removeFileBackupJob)
        file_btn_layout.addWidget(remove_job_btn)

        test_dest_btn = QPushButton("Test Destination")
        test_dest_btn.setMinimumHeight(40)
        test_dest_btn.clicked.connect(self.testBackupDestination)
        file_btn_layout.addWidget(test_dest_btn)

        run_backup_btn = QPushButton("Run Backup Now")
        run_backup_btn.setMinimumHeight(40)
        run_backup_btn.setStyleSheet("QPushButton { background-color: #a6e3a1; color: #1e1e2e; } QPushButton:hover { background-color: #94d888; }")
        run_backup_btn.clicked.connect(self.runFileBackup)
        file_btn_layout.addWidget(run_backup_btn)

        restore_btn = QPushButton("Restore Selected...")
        restore_btn.setMinimumHeight(40)
        restore_btn.setStyleSheet("QPushButton { background-color: #f38ba8; color: #1e1e2e; } QPushButton:hover { background-color: #eb6f92; }")
        restore_btn.clicked.connect(self.restoreSelectedFileBackup)
        file_btn_layout.addWidget(restore_btn)

        self.file_backup_cancel_btn = QPushButton("Cancel")
        self.file_backup_cancel_btn.setMinimumHeight(40)
        self.file_backup_cancel_btn.setEnabled(False)
        self.file_backup_cancel_btn.clicked.connect(self.cancelFileBackup)
        file_btn_layout.addWidget(self.file_backup_cancel_btn)

        layout.addLayout(file_btn_layout)

        self.loadFileBackupJobs()

        # ---- Product Key & Drivers ----
        extras_title = QLabel("System Recovery Extras")
        extras_title.setFont(QFont("Arial", 12, QFont.Bold))
        extras_title.setStyleSheet("color: #a6adc8; margin-top: 10px;")
        layout.addWidget(extras_title)

        key_card = QGroupBox("Product Key && Drivers")
        key_layout = QHBoxLayout()

        show_key_btn = QPushButton("Show / Save Product Key")
        show_key_btn.setMinimumHeight(40)
        show_key_btn.clicked.connect(self.showProductKey)
        key_layout.addWidget(show_key_btn)

        backup_drivers_btn = QPushButton("Backup Drivers (DISM)")
        backup_drivers_btn.setMinimumHeight(40)
        backup_drivers_btn.setToolTip("Exports all third-party installed drivers - useful before a reinstall.")
        backup_drivers_btn.clicked.connect(self.backupDrivers)
        key_layout.addWidget(backup_drivers_btn)

        key_card.setLayout(key_layout)
        layout.addWidget(key_card)

        # ---- Browser Data ----
        browser_card = QGroupBox("Browser Data (Chrome, Edge, Firefox)")
        browser_layout = QHBoxLayout()

        backup_browser_btn = QPushButton("Backup Browser Data")
        backup_browser_btn.setMinimumHeight(40)
        backup_browser_btn.clicked.connect(self.backupBrowserData)
        browser_layout.addWidget(backup_browser_btn)

        restore_browser_btn = QPushButton("Restore Browser Data...")
        restore_browser_btn.setMinimumHeight(40)
        restore_browser_btn.setStyleSheet("QPushButton { background-color: #f38ba8; color: #1e1e2e; } QPushButton:hover { background-color: #eb6f92; }")
        restore_browser_btn.clicked.connect(self.restoreBrowserData)
        browser_layout.addWidget(restore_browser_btn)

        browser_card.setLayout(browser_layout)
        layout.addWidget(browser_card)

        # ---- Full Registry Export ----
        reg_export_card = QGroupBox("Full Registry Export (.reg)")
        reg_export_layout = QHBoxLayout()

        reg_export_info = QLabel("Real .reg files you can double-click to restore - separate from the JSON snapshots above.")
        reg_export_info.setWordWrap(True)
        reg_export_layout.addWidget(reg_export_info, 1)

        export_reg_btn = QPushButton("Export Registry...")
        export_reg_btn.setMinimumHeight(40)
        export_reg_btn.clicked.connect(self.exportFullRegistry)
        reg_export_layout.addWidget(export_reg_btn)

        import_reg_btn = QPushButton("Import .reg File...")
        import_reg_btn.setMinimumHeight(40)
        import_reg_btn.setStyleSheet("QPushButton { background-color: #f38ba8; color: #1e1e2e; } QPushButton:hover { background-color: #eb6f92; }")
        import_reg_btn.clicked.connect(self.importRegistryFile)
        reg_export_layout.addWidget(import_reg_btn)

        reg_export_card.setLayout(reg_export_layout)
        layout.addWidget(reg_export_card)

        self.backup_action_buttons = [
            run_backup_btn, restore_btn, show_key_btn, backup_drivers_btn,
            backup_browser_btn, restore_browser_btn, export_reg_btn, import_reg_btn,
        ]

        # Log - every action on this page (file backup/restore, browser data,
        # drivers, registry export/import) streams its progress here so
        # something is always visibly happening.
        backup_log_title = QLabel("Activity Log")
        backup_log_title.setFont(QFont("Arial", 12, QFont.Bold))
        backup_log_title.setStyleSheet("color: #a6adc8; margin-top: 10px;")
        layout.addWidget(backup_log_title)

        self.backup_log = QTextEdit()
        self.backup_log.setReadOnly(True)
        self.backup_log.setMaximumHeight(150)
        self.backup_log.setText("Ready. Backup and restore progress streams here.")
        layout.addWidget(self.backup_log)

        return page

    def createSettingsPage(self):
        """Create settings page"""
        page = QWidget()
        layout = QVBoxLayout()
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(15)
        page.setLayout(layout)
        
        # Title
        title = QLabel("Settings")
        title.setFont(QFont("Arial", 18, QFont.Bold))
        layout.addWidget(title)
        
        # Admin status
        admin_card = QGroupBox("Administrator Status")
        admin_layout = QVBoxLayout()
        
        if self.is_admin:
            status_text = "Running with Administrator privileges\n\nAll features are enabled."
        else:
            status_text = (
                "Running in User mode\n\n"
                "Some features require Administrator privileges:\n"
                "• Registry fixes\n"
                "• Startup item changes\n"
                "• System-wide file cleaning"
            )
        
        status_label = QLabel(status_text)
        status_label.setWordWrap(True)
        admin_layout.addWidget(status_label)
        
        if not self.is_admin:
            restart_btn = QPushButton("Restart as Administrator")
            restart_btn.setMinimumHeight(50)
            restart_btn.clicked.connect(self.restartAsAdmin)
            restart_btn.setStyleSheet("QPushButton { background-color: #fab387; color: #1e1e2e; } QPushButton:hover { background-color: #f5a26a; }")
            admin_layout.addWidget(restart_btn)
        
        admin_card.setLayout(admin_layout)
        layout.addWidget(admin_card)
        
        # Backup settings
        backup_card = QGroupBox("Backup Settings")
        backup_layout = QVBoxLayout()
        
        dir_layout = QHBoxLayout()
        dir_layout.addWidget(QLabel("Location:"))
        
        self.backup_path_label = QLabel(self.backup_dir)
        self.backup_path_label.setWordWrap(True)
        dir_layout.addWidget(self.backup_path_label, 1)
        
        change_btn = QPushButton("Change")
        change_btn.clicked.connect(self.changeBackupDir)
        dir_layout.addWidget(change_btn)
        
        backup_layout.addLayout(dir_layout)
        backup_card.setLayout(backup_layout)
        layout.addWidget(backup_card)

        # Scan & clean behavior
        scan_card = QGroupBox("Scan && Clean Behavior")
        scan_layout = QVBoxLayout()

        auto_backup_cb = QCheckBox("Automatically back up the registry before applying fixes")
        auto_backup_cb.setChecked(self.settings.get('auto_backup_before_fix', True))
        auto_backup_cb.stateChanged.connect(lambda state: self.updateSetting('auto_backup_before_fix', state))
        scan_layout.addWidget(auto_backup_cb)

        skip_sys_cb = QCheckBox("Skip hidden system/update components in scans (recommended)")
        skip_sys_cb.setChecked(self.settings.get('skip_system_components', True))
        skip_sys_cb.setToolTip("These are OS-managed entries Programs and Features also hides - "
                                "turning this off will show far more results, most of them not "
                                "meant to be touched.")
        skip_sys_cb.stateChanged.connect(lambda state: self.updateSetting('skip_system_components', state))
        scan_layout.addWidget(skip_sys_cb)

        scan_card.setLayout(scan_layout)
        layout.addWidget(scan_card)

        # Startup behavior
        startup_card = QGroupBox("Startup Behavior")
        startup_layout = QVBoxLayout()

        admin_launch_cb = QCheckBox("Launch REGwintool in Administrator mode automatically")
        admin_launch_cb.setChecked(self.settings.get('launch_as_admin', False))
        admin_launch_cb.setToolTip("Takes effect the next time you start REGwintool - "
                                    "you'll get a UAC prompt on every launch.")
        admin_launch_cb.stateChanged.connect(lambda state: self.updateSetting('launch_as_admin', state))
        startup_layout.addWidget(admin_launch_cb)

        remember_page_cb = QCheckBox("Reopen on the last page I was viewing")
        remember_page_cb.setChecked(self.settings.get('remember_last_page', False))
        remember_page_cb.stateChanged.connect(lambda state: self.updateSetting('remember_last_page', state))
        startup_layout.addWidget(remember_page_cb)

        auto_scan_cb = QCheckBox("Automatically scan the registry on launch")
        auto_scan_cb.setChecked(self.settings.get('auto_scan_registry_on_launch', False))
        auto_scan_cb.stateChanged.connect(lambda state: self.updateSetting('auto_scan_registry_on_launch', state))
        startup_layout.addWidget(auto_scan_cb)

        startup_card.setLayout(startup_layout)
        layout.addWidget(startup_card)

        # Appearance
        appearance_card = QGroupBox("Appearance")
        appearance_layout = QHBoxLayout()
        appearance_layout.addWidget(QLabel("Text size:"))

        font_combo = QComboBox()
        font_sizes = [('Small', 9), ('Medium', 10), ('Large', 12), ('Extra Large', 14)]
        current_size = self.settings.get('font_size', 10)
        for i, (label, size) in enumerate(font_sizes):
            font_combo.addItem(label, size)
            if size == current_size:
                font_combo.setCurrentIndex(i)
        font_combo.currentIndexChanged.connect(lambda idx, combo=font_combo: self.updateFontSize(combo.itemData(idx)))
        appearance_layout.addWidget(font_combo)
        appearance_layout.addStretch()

        appearance_card.setLayout(appearance_layout)
        layout.addWidget(appearance_card)

        # About
        about_card = QGroupBox("About REGwintool")
        about_layout = QVBoxLayout()
        
        mode = "ADMIN" if self.is_admin else "USER"
        about_html = f"""
        <div style='padding: 10px;'>
            <h3 style='margin: 0 0 10px 0;'>REGwintool v2.1</h3>
            <p style='margin: 5px 0;'><b>Mode:</b> {mode}</p>
            <p style='margin: 5px 0;'><b>Developed by:</b> Ronald Goodchild</p>
            <p style='margin: 5px 0;'><b>Company:</b> REGTeches</p>
            <p style='margin: 10px 0 5px 0;'><b>Features:</b></p>
            <ul style='margin: 0; padding-left: 20px;'>
                <li>Registry Cleaner</li>
                <li>Junk File Remover</li>
                <li>Startup Manager</li>
                <li>Privacy Tools</li>
                <li>Program Uninstaller & Software Installer (winget)</li>
                <li>Repair Tools (Windows Update, Defender, SFC/DISM)</li>
                <li>Backup System (settings, files, drivers, browser data, registry)</li>
                <li>System Tools & Windows Tweaks</li>
            </ul>
            <p style='margin: 10px 0 0 0;'>Built with Python & PyQt5</p>
            <p style='margin: 5px 0 0 0;'>© 2024 REGTeches</p>
        </div>
        """
        
        about_label = QLabel(about_html)
        about_label.setWordWrap(True)
        about_layout.addWidget(about_label)
        about_card.setLayout(about_layout)
        layout.addWidget(about_card)
        
        layout.addStretch()
        
        return page
    
    # ==================== REGISTRY FUNCTIONS ====================
    
    def scanRegistry(self):
        """Scan Windows registry"""
        if self.scan_thread is not None and self.scan_thread.isRunning():
            self.status_bar.showMessage('A scan is already running...', 2000)
            return
        self.status_bar.showMessage('Scanning registry...')
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(0)
        
        self.reg_table.setRowCount(0)
        
        self.scan_thread = ScanThread(self.performRegistryScan)
        self.scan_thread.progress.connect(self.updateProgress)
        self.scan_thread.finished.connect(self.displayRegistryResults)
        self.scan_thread.start()
    
    def performRegistryScan(self, progress_callback):
        """Actually perform the registry scan"""
        issues = []
        
        progress_callback("Scanning uninstall entries...", 25)
        issues.extend(self.scanUninstallEntries())
        
        progress_callback("Scanning startup entries...", 50)
        issues.extend(self.scanStartupRegistry())
        
        progress_callback("Scanning file extensions...", 75)
        issues.extend(self.scanFileExtensions())
        
        progress_callback("Scan complete", 100)
        
        return issues
    
    def scanUninstallEntries(self):
        """Scan for invalid uninstall entries"""
        issues = []
        
        paths = [
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
            (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
        ]
        
        for hive, path in paths:
            try:
                key = winreg.OpenKey(hive, path, 0, winreg.KEY_READ)

                for i in range(winreg.QueryInfoKey(key)[0]):
                    try:
                        subkey_name = winreg.EnumKey(key, i)
                        subkey_path = f"{path}\\{subkey_name}"

                        try:
                            subkey = winreg.OpenKey(hive, subkey_path, 0, winreg.KEY_READ)

                            display_name = None
                            uninstall_string = None
                            system_component = 0

                            try:
                                display_name = winreg.QueryValueEx(subkey, "DisplayName")[0]
                            except Exception:
                                pass

                            try:
                                uninstall_string = winreg.QueryValueEx(subkey, "UninstallString")[0]
                            except Exception:
                                pass

                            try:
                                system_component = winreg.QueryValueEx(subkey, "SystemComponent")[0]
                            except Exception:
                                pass

                            # Skip hidden OS/update components - they rarely have a
                            # user-facing uninstaller and aren't something to flag.
                            skip_hidden = self.settings.get('skip_system_components', True)
                            if display_name and uninstall_string and not (system_component and skip_hidden):
                                if not command_target_exists(uninstall_string):
                                    issues.append({
                                        'type': 'Invalid Uninstaller',
                                        'name': display_name,
                                        'problem': 'Uninstaller not found',
                                        'location': subkey_path,
                                        'hive': hive,
                                        'fix_type': 'delete_key'
                                    })
                            
                            winreg.CloseKey(subkey)
                        except Exception:
                            pass
                    except Exception:
                        continue
                
                winreg.CloseKey(key)
            except Exception:
                pass
        
        return issues
    
    def scanStartupRegistry(self):
        """Scan startup registry entries"""
        issues = []
        
        paths = [
            (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Run"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Run"),
        ]
        
        for hive, path in paths:
            try:
                key = winreg.OpenKey(hive, path, 0, winreg.KEY_READ)
                
                for i in range(winreg.QueryInfoKey(key)[1]):
                    try:
                        name, value, vtype = winreg.EnumValue(key, i)

                        if not name.startswith('_DISABLED_') and isinstance(value, str) and value.strip():
                            if not command_target_exists(value):
                                issues.append({
                                    'type': 'Invalid Startup',
                                    'name': name,
                                    'problem': 'Program not found',
                                    'location': path,
                                    'hive': hive,
                                    'value_name': name,
                                    'fix_type': 'delete_value'
                                })
                    except Exception:
                        continue
                
                winreg.CloseKey(key)
            except Exception:
                pass
        
        return issues
    
    def scanFileExtensions(self):
        """Scan file extension associations"""
        issues = []
        common_exts = ['.txt', '.doc', '.docx', '.pdf', '.jpg', '.jpeg', '.png', '.mp3', '.mp4', '.zip']
        
        for ext in common_exts:
            try:
                key = winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, ext, 0, winreg.KEY_READ)
                prog_id = winreg.QueryValue(key, "")
                
                if prog_id:
                    try:
                        prog_key = winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, prog_id, 0, winreg.KEY_READ)
                        winreg.CloseKey(prog_key)
                    except FileNotFoundError:
                        issues.append({
                            'type': 'Orphaned Extension',
                            'name': ext,
                            'problem': f'Points to missing ProgID: {prog_id}',
                            'location': ext,
                            'hive': winreg.HKEY_CLASSES_ROOT,
                            'fix_type': 'delete_key'
                        })
                
                winreg.CloseKey(key)
            except Exception:
                pass
        
        return issues
    
    def displayRegistryResults(self, issues):
        """Display registry scan results"""
        self.progress_bar.setVisible(False)
        self.scan_results = issues
        
        self.reg_table.setRowCount(len(issues))
        
        for row, issue in enumerate(issues):
            # Checkbox
            checkbox = QCheckBox()
            checkbox.setChecked(True)
            checkbox_widget = QWidget()
            checkbox_layout = QHBoxLayout(checkbox_widget)
            checkbox_layout.addWidget(checkbox)
            checkbox_layout.setAlignment(Qt.AlignCenter)
            checkbox_layout.setContentsMargins(0, 0, 0, 0)
            self.reg_table.setCellWidget(row, 0, checkbox_widget)
            
            # Data
            self.reg_table.setItem(row, 1, QTableWidgetItem(issue['type']))
            self.reg_table.setItem(row, 2, QTableWidgetItem(issue['name']))
            self.reg_table.setItem(row, 3, QTableWidgetItem(issue['problem']))
            self.reg_table.setItem(row, 4, QTableWidgetItem(issue['location']))
        
        self.reg_table.resizeColumnsToContents()
        
        if issues:
            self.fix_reg_btn.setEnabled(self.is_admin)
            self.reg_summary.setText(f"Scan complete: Found {len(issues)} issues")
            self.status_bar.showMessage(f'Found {len(issues)} issues')
            self.logActivity(f'Registry scan: {len(issues)} issues found')
        else:
            self.fix_reg_btn.setEnabled(False)
            self.reg_summary.setText("Scan complete: Registry is clean!")
            self.status_bar.showMessage('Registry is clean')
            self.logActivity('Registry scan: No issues found')
    
    def selectAllRegistry(self):
        """Select all registry issues"""
        for row in range(self.reg_table.rowCount()):
            checkbox_widget = self.reg_table.cellWidget(row, 0)
            if checkbox_widget:
                checkbox = checkbox_widget.findChild(QCheckBox)
                if checkbox:
                    checkbox.setChecked(True)
    
    def deselectAllRegistry(self):
        """Deselect all registry issues"""
        for row in range(self.reg_table.rowCount()):
            checkbox_widget = self.reg_table.cellWidget(row, 0)
            if checkbox_widget:
                checkbox = checkbox_widget.findChild(QCheckBox)
                if checkbox:
                    checkbox.setChecked(False)
    
    def fixRegistry(self):
        """Fix selected registry issues"""
        
        selected_issues = []
        
        for row in range(self.reg_table.rowCount()):
            checkbox_widget = self.reg_table.cellWidget(row, 0)
            if checkbox_widget:
                checkbox = checkbox_widget.findChild(QCheckBox)
                if checkbox and checkbox.isChecked():
                    selected_issues.append(self.scan_results[row])
        
        if not selected_issues:
            QMessageBox.warning(self, 'No Selection', 'Please select issues to fix!')
            return
        
        auto_backup = self.settings.get('auto_backup_before_fix', True)
        backup_note = 'A backup will be created automatically before making changes.' if auto_backup \
            else 'Auto-backup is turned off in Settings - these changes will NOT be backed up first.'
        reply = QMessageBox.question(
            self,
            'Confirm Registry Fix',
            f'Fix {len(selected_issues)} registry issues?\n\n{backup_note}',
            QMessageBox.Yes | QMessageBox.No
        )

        if reply == QMessageBox.Yes:
            # Create backup (unless disabled in Settings)
            backup_file = self.createRegistryBackup() if auto_backup else None

            fixed = 0
            failed = 0
            
            for issue in selected_issues:
                try:
                    success = self.fixRegistryIssue(issue)
                    if success:
                        fixed += 1
                    else:
                        failed += 1
                except Exception:
                    failed += 1
            
            QMessageBox.information(
                self,
                'Fix Complete',
                f'Fixed: {fixed}\n'
                f'Failed: {failed}\n\n'
                f'Backup saved to:\n{os.path.basename(backup_file) if backup_file else "No backup created"}'
            )
            
            self.logActivity(f'Registry fix: {fixed} fixed, {failed} failed')
            
            # Rescan
            self.scanRegistry()
    
    def fixRegistryIssue(self, issue):
        """Fix a single registry issue"""
        try:
            if issue['fix_type'] == 'delete_key':
                return recursive_delete_key(issue['hive'], issue['location'])
            elif issue['fix_type'] == 'delete_value':
                flags = winreg.KEY_ALL_ACCESS | winreg.KEY_WOW64_64KEY
                with winreg.OpenKey(issue['hive'], issue['location'], 0, flags) as key:
                    winreg.DeleteValue(key, issue['value_name'])
                return True
            return False
        except FileNotFoundError:
            return True   # already gone - not a failure
        except Exception:
            return False
    
    # ==================== FILE CLEANING FUNCTIONS ====================
    
    def scanJunkFiles(self):
        """Scan for junk files"""
        if self.scan_thread is not None and self.scan_thread.isRunning():
            self.status_bar.showMessage('A scan is already running...', 2000)
            return
        self.status_bar.showMessage('Scanning for junk files...')
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(0)
        
        self.files_table.setRowCount(0)
        
        self.scan_thread = ScanThread(self.performFileScan)
        self.scan_thread.progress.connect(self.updateProgress)
        self.scan_thread.finished.connect(self.displayFileResults)
        self.scan_thread.start()
    
    def performFileScan(self, progress_callback):
        """Perform file scan"""
        results = []
        
        # User temp
        progress_callback("Scanning user temp...", 20)
        temp_folder = os.environ.get('TEMP', '')
        if temp_folder and os.path.exists(temp_folder):
            temp_size = self.getFolderSize(temp_folder)
            temp_count = self.countFiles(temp_folder)
            if temp_size > 0:
                results.append({
                    'type': 'User Temp Files',
                    'location': temp_folder,
                    'size': temp_size,
                    'count': temp_count,
                    'deletable': True
                })
        
        # Windows temp
        progress_callback("Scanning Windows temp...", 40)
        win_temp = os.path.join(os.environ.get('WINDIR', 'C:\\Windows'), 'Temp')
        if os.path.exists(win_temp):
            win_temp_size = self.getFolderSize(win_temp)
            win_temp_count = self.countFiles(win_temp)
            if win_temp_size > 0:
                results.append({
                    'type': 'Windows Temp',
                    'location': win_temp,
                    'size': win_temp_size,
                    'count': win_temp_count,
                    'deletable': True
                })
        
        # Recycle bin
        progress_callback("Scanning recycle bin...", 60)
        recycle_size = self.getRecycleBinSize()
        if recycle_size > 0:
            results.append({
                'type': 'Recycle Bin',
                'location': 'System drives',
                'size': recycle_size,
                'count': 'Unknown',
                'deletable': True,
                'special': 'recycle_bin'
            })
        
        # Prefetch
        progress_callback("Scanning prefetch...", 80)
        prefetch = os.path.join(os.environ.get('WINDIR', 'C:\\Windows'), 'Prefetch')
        if os.path.exists(prefetch):
            prefetch_size = self.getFolderSize(prefetch)
            prefetch_count = self.countFiles(prefetch)
            if prefetch_size > 0:
                results.append({
                    'type': 'Prefetch Files',
                    'location': prefetch,
                    'size': prefetch_size,
                    'count': prefetch_count,
                    'deletable': True
                })
        
        progress_callback("Scan complete", 100)
        
        return results
    
    def getFolderSize(self, folder):
        """Get total folder size in bytes"""
        total = 0
        try:
            for entry in os.scandir(folder):
                try:
                    if entry.is_file(follow_symlinks=False):
                        total += entry.stat().st_size
                    elif entry.is_dir(follow_symlinks=False):
                        total += self.getFolderSize(entry.path)
                except Exception:
                    pass
        except Exception:
            pass
        
        return total
    
    def countFiles(self, folder):
        """Count files in folder"""
        count = 0
        try:
            for entry in os.scandir(folder):
                try:
                    if entry.is_file(follow_symlinks=False):
                        count += 1
                    elif entry.is_dir(follow_symlinks=False):
                        count += self.countFiles(entry.path)
                except Exception:
                    pass
        except Exception:
            pass
        
        return count
    
    def getRecycleBinSize(self):
        """Get recycle bin size"""
        total = 0
        for drive in ['C:', 'D:', 'E:', 'F:']:
            recycle = os.path.join(drive + '\\', '$RECYCLE.BIN')
            if os.path.exists(recycle):
                total += self.getFolderSize(recycle)
        return total
    
    def displayFileResults(self, results):
        """Display file scan results"""
        self.progress_bar.setVisible(False)
        self.file_results = results
        
        self.files_table.setRowCount(len(results))
        
        total_size = 0
        
        for row, item in enumerate(results):
            size_mb = item['size'] / (1024 * 1024)
            total_size += size_mb
            
            # Checkbox
            checkbox = QCheckBox()
            checkbox.setChecked(True)
            checkbox_widget = QWidget()
            checkbox_layout = QHBoxLayout(checkbox_widget)
            checkbox_layout.addWidget(checkbox)
            checkbox_layout.setAlignment(Qt.AlignCenter)
            checkbox_layout.setContentsMargins(0, 0, 0, 0)
            self.files_table.setCellWidget(row, 0, checkbox_widget)
            
            # Data
            self.files_table.setItem(row, 1, QTableWidgetItem(item['type']))
            self.files_table.setItem(row, 2, QTableWidgetItem(item['location']))
            self.files_table.setItem(row, 3, QTableWidgetItem(f'{size_mb:.2f} MB'))
            self.files_table.setItem(row, 4, QTableWidgetItem(str(item['count'])))
        
        self.files_table.resizeColumnsToContents()
        self.size_label.setText(f'Total: {total_size:.2f} MB ({total_size/1024:.2f} GB)')
        
        if results:
            self.clean_files_btn.setEnabled(True)
            self.status_bar.showMessage(f'Found {total_size:.2f} MB of junk')
            self.logActivity(f'Junk scan: {total_size:.2f} MB found')
        else:
            self.clean_files_btn.setEnabled(False)
            self.status_bar.showMessage('No junk files found')
    
    def selectAllFiles(self):
        """Select all files"""
        for row in range(self.files_table.rowCount()):
            checkbox_widget = self.files_table.cellWidget(row, 0)
            if checkbox_widget:
                checkbox = checkbox_widget.findChild(QCheckBox)
                if checkbox:
                    checkbox.setChecked(True)
    
    def deselectAllFiles(self):
        """Deselect all files"""
        for row in range(self.files_table.rowCount()):
            checkbox_widget = self.files_table.cellWidget(row, 0)
            if checkbox_widget:
                checkbox = checkbox_widget.findChild(QCheckBox)
                if checkbox:
                    checkbox.setChecked(False)
    
    def cleanFiles(self):
        """Clean selected junk files"""
        if self.clean_thread is not None and self.clean_thread.isRunning():
            QMessageBox.information(self, 'Busy', 'A cleaning operation is already running.')
            return

        selected_items = []

        for row in range(self.files_table.rowCount()):
            checkbox_widget = self.files_table.cellWidget(row, 0)
            if checkbox_widget:
                checkbox = checkbox_widget.findChild(QCheckBox)
                if checkbox and checkbox.isChecked():
                    selected_items.append(self.file_results[row])
        
        if not selected_items:
            QMessageBox.warning(self, 'No Selection', 'Please select files to clean!')
            return
        
        total_size = sum(item['size'] for item in selected_items) / (1024 * 1024)
        
        reply = QMessageBox.question(
            self,
            'Confirm Deletion',
            f'Delete {len(selected_items)} categories of files?\n'
            f'Total size: {total_size:.2f} MB\n\n'
            'WARNING: THIS ACTION CANNOT BE UNDONE!',
            QMessageBox.Yes | QMessageBox.No
        )
        
        if reply == QMessageBox.Yes:
            self.progress_bar.setVisible(True)
            self.progress_bar.setValue(0)

            self.clean_thread = CleanThread(self.performCleanFiles, selected_items)
            self.clean_thread.progress.connect(self.updateProgress)
            self.clean_thread.finished.connect(self.displayCleanResults)
            self.clean_thread.start()
    
    def performCleanFiles(self, items, progress_callback):
        """Actually delete files"""
        results = {'success': 0, 'failed': 0, 'size_freed': 0}
        
        total = len(items)
        
        for i, item in enumerate(items):
            progress_callback(f"Cleaning {item['type']}...", int((i / total) * 100))
            
            try:
                if item.get('special') == 'recycle_bin':
                    # Empty recycle bin
                    try:
                        ctypes.windll.shell32.SHEmptyRecycleBinW(None, None, 0)
                        results['success'] += 1
                        results['size_freed'] += item['size']
                    except Exception:
                        results['failed'] += 1
                else:
                    # Delete folder contents
                    if os.path.exists(item['location']):
                        deleted_count = 0
                        for entry in os.scandir(item['location']):
                            try:
                                if entry.is_file(follow_symlinks=False):
                                    os.remove(entry.path)
                                    deleted_count += 1
                                elif entry.is_dir(follow_symlinks=False):
                                    shutil.rmtree(entry.path)
                                    deleted_count += 1
                            except Exception:
                                pass
                        
                        if deleted_count > 0:
                            results['success'] += 1
                            results['size_freed'] += item['size']
                        else:
                            results['failed'] += 1
                    else:
                        results['failed'] += 1
            except Exception as e:
                print(f"Error cleaning {item['type']}: {e}")
                results['failed'] += 1
        
        progress_callback("Cleaning complete", 100)
        
        return results
    
    def displayCleanResults(self, results):
        """Display cleaning results"""
        self.progress_bar.setVisible(False)
        
        size_mb = results['size_freed'] / (1024 * 1024)
        size_gb = size_mb / 1024
        
        QMessageBox.information(
            self,
            'Cleaning Complete',
            f"Successfully cleaned: {results['success']}\n"
            f"Failed: {results['failed']}\n\n"
            f"Space freed: {size_mb:.2f} MB ({size_gb:.2f} GB)"
        )
        
        self.logActivity(f'Files cleaned: {size_mb:.2f} MB freed')
        
        # Rescan
        self.scanJunkFiles()
    
    # ==================== STARTUP MANAGER FUNCTIONS ====================
    
    def loadStartupItems(self):
        """Load startup programs"""
        self.status_bar.showMessage('Loading startup items...')
        self.startup_table.setRowCount(0)
        
        items = []
        
        paths = [
            (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Run", "HKCU Run"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Run", "HKLM Run"),
        ]
        
        for hive, path, location in paths:
            try:
                key = winreg.OpenKey(hive, path, 0, winreg.KEY_READ)
                
                for i in range(winreg.QueryInfoKey(key)[1]):
                    try:
                        name, value, vtype = winreg.EnumValue(key, i)
                        
                        disabled = name.startswith('_DISABLED_')
                        actual_name = name.replace('_DISABLED_', '') if disabled else name
                        
                        impact = self.getStartupImpact(value)
                        
                        items.append({
                            'name': actual_name,
                            'publisher': 'Unknown',
                            'status': 'Disabled' if disabled else 'Enabled',
                            'impact': impact,
                            'location': location,
                            'path': value,
                            'hive': hive,
                            'reg_path': path,
                            'reg_name': name
                        })
                    except Exception:
                        continue
                
                winreg.CloseKey(key)
            except Exception:
                pass
        
        # Display
        self.startup_items = items
        self.startup_table.setRowCount(len(items))
        
        for row, item in enumerate(items):
            self.startup_table.setItem(row, 0, QTableWidgetItem(item['name']))
            self.startup_table.setItem(row, 1, QTableWidgetItem(item['publisher']))
            
            # NOTE: the app-wide stylesheet sets "QTableWidget::item { color: #cdd6f4; }",
            # which silently overrides QTableWidgetItem.setForeground() everywhere (a known
            # Qt stylesheet quirk - item-level QSS "color" wins over the ForegroundRole).
            # setBackground() is NOT overridden, so status/severity are shown as background
            # chips dark enough that the fixed #cdd6f4 text stays readable on top of them,
            # instead of relying on foreground color (which would silently do nothing).
            status_item = QTableWidgetItem(item['status'])
            if item['status'] == 'Enabled':
                status_item.setBackground(QColor(30, 61, 46))     # dark green chip
            else:
                status_item.setBackground(QColor(77, 35, 51))     # dark red chip
            status_item.setTextAlignment(Qt.AlignCenter)
            self.startup_table.setItem(row, 2, status_item)

            impact_item = QTableWidgetItem(item['impact'])
            if item['impact'] == 'High':
                impact_item.setBackground(QColor(77, 35, 51))     # dark red chip
            elif item['impact'] == 'Medium':
                impact_item.setBackground(QColor(77, 56, 32))     # dark peach chip
            elif item['impact'] == 'Low':
                impact_item.setBackground(QColor(30, 61, 46))     # dark green chip
            impact_item.setTextAlignment(Qt.AlignCenter)
            self.startup_table.setItem(row, 3, impact_item)
            
            self.startup_table.setItem(row, 4, QTableWidgetItem(item['location']))
        
        self.startup_table.resizeColumnsToContents()
        self.status_bar.showMessage(f'Loaded {len(items)} startup items')
        self.logActivity(f'Startup: {len(items)} items loaded')
    
    def getStartupImpact(self, path):
        """Estimate startup impact based on file size"""
        if not isinstance(path, str) or not path.strip():
            return 'Unknown'
        try:
            exe_path = path.strip('"').split()[0]
            if os.path.exists(exe_path):
                size = os.path.getsize(exe_path) / (1024 * 1024)  # MB
                if size > 50:
                    return 'High'
                elif size > 10:
                    return 'Medium'
                else:
                    return 'Low'
        except Exception:
            pass
        
        return 'Unknown'
    
    def disableStartup(self):
        """Disable startup item"""
        
        selected = self.startup_table.currentRow()
        if selected < 0:
            QMessageBox.warning(self, 'No Selection', 'Please select a startup item!')
            return
        
        item = self.startup_items[selected]
        
        if item['status'] == 'Disabled':
            QMessageBox.information(self, 'Already Disabled', 'This item is already disabled.')
            return
        
        try:
            key = winreg.OpenKey(item['hive'], item['reg_path'], 0, winreg.KEY_ALL_ACCESS)
            value, vtype = winreg.QueryValueEx(key, item['reg_name'])
            winreg.DeleteValue(key, item['reg_name'])
            winreg.SetValueEx(key, f"_DISABLED_{item['reg_name']}", 0, vtype, value)
            winreg.CloseKey(key)

            QMessageBox.information(self, 'Success', f'{item["name"]} has been disabled.')
            self.logActivity(f'Startup disabled: {item["name"]}')
            self.loadStartupItems()
            
        except PermissionError:
            QMessageBox.critical(self, 'Permission Denied', 'Administrator rights required!')
        except Exception as e:
            QMessageBox.critical(self, 'Error', f'Failed: {str(e)}')
    
    def enableStartup(self):
        """Enable startup item"""
        
        selected = self.startup_table.currentRow()
        if selected < 0:
            QMessageBox.warning(self, 'No Selection', 'Please select a startup item!')
            return
        
        item = self.startup_items[selected]
        
        if item['status'] == 'Enabled':
            QMessageBox.information(self, 'Already Enabled', 'This item is already enabled.')
            return
        
        try:
            key = winreg.OpenKey(item['hive'], item['reg_path'], 0, winreg.KEY_ALL_ACCESS)
            value, vtype = winreg.QueryValueEx(key, item['reg_name'])
            winreg.DeleteValue(key, item['reg_name'])
            original_name = item['reg_name'].replace('_DISABLED_', '')
            winreg.SetValueEx(key, original_name, 0, vtype, value)
            winreg.CloseKey(key)

            QMessageBox.information(self, 'Success', f'{item["name"]} has been enabled.')
            self.logActivity(f'Startup enabled: {item["name"]}')
            self.loadStartupItems()
            
        except PermissionError:
            QMessageBox.critical(self, 'Permission Denied', 'Administrator rights required!')
        except Exception as e:
            QMessageBox.critical(self, 'Error', f'Failed: {str(e)}')
    
    def deleteStartup(self):
        """Delete startup item"""
        
        selected = self.startup_table.currentRow()
        if selected < 0:
            QMessageBox.warning(self, 'No Selection', 'Please select a startup item!')
            return
        
        item = self.startup_items[selected]
        
        reply = QMessageBox.question(
            self,
            'Confirm Deletion',
            f'Permanently delete {item["name"]} from startup?\n\nThis cannot be undone!',
            QMessageBox.Yes | QMessageBox.No
        )
        
        if reply == QMessageBox.Yes:
            try:
                key = winreg.OpenKey(item['hive'], item['reg_path'], 0, winreg.KEY_WRITE)
                winreg.DeleteValue(key, item['reg_name'])
                winreg.CloseKey(key)
                
                QMessageBox.information(self, 'Success', f'{item["name"]} has been deleted.')
                self.logActivity(f'Startup deleted: {item["name"]}')
                self.loadStartupItems()
                
            except PermissionError:
                QMessageBox.critical(self, 'Permission Denied', 'Administrator rights required!')
            except Exception as e:
                QMessageBox.critical(self, 'Error', f'Failed: {str(e)}')
    
    def uninstallStartupProgram(self):
        """Open Windows uninstaller for the selected startup program."""
        selected = self.startup_table.currentRow()
        if selected < 0:
            QMessageBox.warning(self, 'No Selection', 'Please select a startup item!')
            return
        
        item = self.startup_items[selected]
        program_name = item['name']
        
        reply = QMessageBox.question(
            self,
            'Uninstall Program',
            f'Open Windows uninstaller for:\n\n{program_name}\n\nYou will need to find and uninstall the program from the list.',
            QMessageBox.Yes | QMessageBox.No
        )
        
        if reply == QMessageBox.Yes:
            try:
                # Open Programs and Features (appwiz.cpl)
                subprocess.Popen('appwiz.cpl', shell=True)
                self.logActivity(f'Opened uninstaller for: {program_name}')
            except Exception as e:
                QMessageBox.warning(self, 'Error', f'Could not open Programs and Features:\n{str(e)}')
    
    def openWindowsSettings(self):
        """Open Windows Settings - Apps & Features."""
        try:
            # Use explorer.exe to open the ms-settings URI
            subprocess.Popen(['explorer.exe', 'ms-settings:appsfeatures'])
            self.logActivity('Opened: Windows Settings - Apps & Features')
        except Exception as e:
            # Fallback to Control Panel if Settings fails
            try:
                subprocess.Popen('appwiz.cpl', shell=True)
                self.logActivity('Opened: Programs and Features (Control Panel)')
            except Exception:
                QMessageBox.warning(self, 'Error', f'Could not open Windows Settings:\n{str(e)}')
    
    def selectAllPrograms(self):
        """Check every program in the list"""
        for row in range(self.uninstall_table.rowCount()):
            checkbox_widget = self.uninstall_table.cellWidget(row, 0)
            if checkbox_widget:
                checkbox = checkbox_widget.findChild(QCheckBox)
                if checkbox:
                    checkbox.setChecked(True)

    def deselectAllPrograms(self):
        """Uncheck every program in the list"""
        for row in range(self.uninstall_table.rowCount()):
            checkbox_widget = self.uninstall_table.cellWidget(row, 0)
            if checkbox_widget:
                checkbox = checkbox_widget.findChild(QCheckBox)
                if checkbox:
                    checkbox.setChecked(False)

    def uninstallSelectedPrograms(self):
        """Launch the uninstaller for every checked program, one after another."""
        if self.uninstall_thread is not None and self.uninstall_thread.isRunning():
            QMessageBox.information(self, 'Busy', 'An uninstall queue is already running.')
            return

        selected = []
        for row in range(self.uninstall_table.rowCount()):
            checkbox_widget = self.uninstall_table.cellWidget(row, 0)
            if checkbox_widget:
                checkbox = checkbox_widget.findChild(QCheckBox)
                if checkbox and checkbox.isChecked() and row < len(self.installed_programs):
                    selected.append(self.installed_programs[row])

        if not selected:
            QMessageBox.warning(self, 'No Selection', 'Check one or more programs to uninstall!')
            return

        with_command = [p for p in selected if p.get('uninstall_string')]
        without_command = [p for p in selected if not p.get('uninstall_string')]

        if not with_command:
            QMessageBox.warning(self, 'Nothing To Do',
                'None of the selected programs have an uninstall command on record.\n\n'
                'Try "Open Windows Settings" to remove them from there instead.')
            return

        preview = '\n'.join(f'- {p["name"]}' for p in with_command[:15])
        if len(with_command) > 15:
            preview += f'\n... and {len(with_command) - 15} more'
        note = ''
        if without_command:
            note = (f'\n\nNote: {len(without_command)} selected program(s) have no uninstall '
                     'command on record and will be skipped.')

        reply = QMessageBox.question(
            self,
            'Uninstall Selected Programs',
            f'Uninstall {len(with_command)} program(s)?\n\n{preview}\n\n'
            'Each one runs its own uninstall wizard, one at a time - follow the '
            f'prompts for each as it appears.{note}',
            QMessageBox.Yes | QMessageBox.No
        )
        if reply != QMessageBox.Yes:
            return

        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(0)
        self.uninstall_thread = UninstallThread(with_command)
        self.uninstall_thread.progress.connect(self.updateProgress)
        self.uninstall_thread.finished.connect(self.displayUninstallResults)
        self.uninstall_thread.start()

    def displayUninstallResults(self, results):
        """Show the outcome of a bulk uninstall run and refresh the list"""
        self.progress_bar.setVisible(False)
        msg = f"Launched {results['launched']} uninstaller(s)."
        if results['failed']:
            msg += '\n\nFailed to launch:\n' + '\n'.join(results['failed'][:10])
        self.logActivity(f"Programs: bulk uninstall launched {results['launched']}, {len(results['failed'])} failed")
        QMessageBox.information(self, 'Uninstall Queue Complete', msg)
        self.loadInstalledPrograms()

    # ---- Install Software (winget) ----

    def appendWingetOutput(self, text):
        self.winget_output.append(text)
        self.winget_output.verticalScrollBar().setValue(self.winget_output.verticalScrollBar().maximum())

    def setWingetButtonsEnabled(self, enabled):
        for btn in self.winget_buttons:
            btn.setEnabled(enabled)

    def wingetSearch(self):
        """Search available packages with winget"""
        query = self.winget_query.text().strip()
        if not query:
            QMessageBox.warning(self, 'No Search Term', 'Enter a package name or ID to search for.')
            return
        if self.command_thread is not None and self.command_thread.isRunning():
            QMessageBox.information(self, 'Busy', 'Another operation is already running - see the log below for progress.')
            return

        self.setWingetButtonsEnabled(False)
        self.status_bar.showMessage(f'Searching winget for "{query}"...')
        self.winget_output.setText(f'Searching for "{query}"...\n')
        steps = [(f'winget search "{query}"', f'winget search "{query}"', 60)]
        self.command_thread = CommandThread(steps)
        self.command_thread.output.connect(self.appendWingetOutput)
        self.command_thread.finished.connect(self._onWingetSearchFinished)
        self.command_thread.start()

    def _onWingetSearchFinished(self):
        self.setWingetButtonsEnabled(True)
        self.status_bar.showMessage('Search complete', 3000)

    def wingetInstall(self):
        """Install the package named in the search box"""
        package = self.winget_query.text().strip()
        if not package:
            QMessageBox.warning(self, 'No Package', 'Enter a package name or ID to install.')
            return
        reply = QMessageBox.question(self, 'Confirm Install', f'Install "{package}" via winget?', QMessageBox.Yes | QMessageBox.No)
        if reply != QMessageBox.Yes:
            return
        if self.command_thread is not None and self.command_thread.isRunning():
            QMessageBox.information(self, 'Busy', 'Another operation is already running - see the log below for progress.')
            return

        self.setWingetButtonsEnabled(False)
        self.status_bar.showMessage(f'Installing "{package}"...')
        self.winget_output.append(f'\nInstalling "{package}"...')
        cmd = f'winget install --id "{package}" --silent --accept-package-agreements --accept-source-agreements'
        steps = [(f'winget install {package}', cmd, 300)]
        self.command_thread = CommandThread(steps)
        self.command_thread.output.connect(self.appendWingetOutput)
        self.command_thread.finished.connect(lambda: self._onWingetInstallFinished(package))
        self.command_thread.start()

    def _onWingetInstallFinished(self, package):
        self.setWingetButtonsEnabled(True)
        self.status_bar.showMessage(f'Install attempt for "{package}" finished', 5000)
        self.appendWingetOutput(f'\n--- Install attempt for "{package}" finished - check the log above for success/failure. ---')
        self.logActivity(f'winget install attempted: {package}')
        QMessageBox.information(self, 'Install Complete', f'Install attempt for "{package}" finished.\n\nCheck the log for details.')

    def wingetUpgradeAll(self):
        """Upgrade every app winget manages in one pass"""
        reply = QMessageBox.question(
            self, 'Upgrade All',
            'Upgrade all installed apps that winget manages?\n\n'
            'This can take a while depending on how many updates are available.',
            QMessageBox.Yes | QMessageBox.No
        )
        if reply != QMessageBox.Yes:
            return
        if self.command_thread is not None and self.command_thread.isRunning():
            QMessageBox.information(self, 'Busy', 'Another operation is already running - see the log below for progress.')
            return

        self.setWingetButtonsEnabled(False)
        self.status_bar.showMessage('Upgrading all installed apps...')
        self.winget_output.setText('Upgrading all installed apps...\n')
        steps = [('winget upgrade --all', 'winget upgrade --all --silent --accept-package-agreements --accept-source-agreements', 1800)]
        self.command_thread = CommandThread(steps)
        self.command_thread.output.connect(self.appendWingetOutput)
        self.command_thread.finished.connect(self._onWingetUpgradeFinished)
        self.command_thread.start()

    def _onWingetUpgradeFinished(self):
        self.setWingetButtonsEnabled(True)
        self.status_bar.showMessage('Upgrade All finished', 5000)
        self.appendWingetOutput('\n--- Upgrade All finished. ---')
        self.logActivity('winget upgrade --all completed')
        QMessageBox.information(self, 'Upgrade Complete', 'winget upgrade --all finished. Check the log for details.')

    # ==================== REPAIR FUNCTIONS ====================
    # Adapted from TechniciansToolkit_V30's Windows Update/Defender/SFC-DISM
    # repair flows. All multi-step operations run through CommandThread so
    # the UI never blocks; log output streams into self.repair_log.

    def appendRepairLog(self, text):
        self.repair_log.append(text)
        self.repair_log.verticalScrollBar().setValue(self.repair_log.verticalScrollBar().maximum())

    def runRepairSteps(self, steps, completion_title='Complete', completion_message='Operation finished.'):
        """Run a list of repair steps in the background and show a summary when done"""
        if self.command_thread is not None and self.command_thread.isRunning():
            QMessageBox.information(self, 'Busy', 'A repair operation is already running - see the log below for progress.')
            return
        for btn in self.repair_buttons:
            btn.setEnabled(False)
        self.status_bar.showMessage(f'{completion_title} - running...')
        self.appendRepairLog(f'\n{"=" * 50}\n{completion_title.upper()} - STARTING\n{"=" * 50}')
        self.command_thread = CommandThread(steps)
        self.command_thread.output.connect(self.appendRepairLog)
        self.command_thread.finished.connect(lambda: self._onRepairFinished(completion_title, completion_message))
        self.command_thread.start()

    def _onRepairFinished(self, title, message):
        for btn in self.repair_buttons:
            btn.setEnabled(True)
        self.status_bar.showMessage(f'{title} - done', 5000)
        self.appendRepairLog(f'\n{"=" * 50}\n{title.upper()} - DONE\n{"=" * 50}\n')
        self.logActivity(f'Repair: {title}')
        QMessageBox.information(self, title, message)

    def windowsUpdateQuickReset(self):
        """Stop Windows Update services, clear the cache folders, restart services"""
        if not self.is_admin:
            QMessageBox.warning(self, 'Admin Required', 'Resetting Windows Update requires Administrator rights.')
            return
        reply = QMessageBox.question(
            self, 'Quick Reset',
            'Stop Windows Update services, rename the update cache folders, and restart the services?',
            QMessageBox.Yes | QMessageBox.No
        )
        if reply != QMessageBox.Yes:
            return

        wu_services = ['wuauserv', 'cryptSvc', 'bits', 'msiserver']
        steps = [
            ('Stopping Windows Update services...', ' & '.join(f'net stop {s}' for s in wu_services), 30),
            ('Renaming update cache folders...',
             'ren C:\\Windows\\SoftwareDistribution SoftwareDistribution.old & '
             'ren C:\\Windows\\System32\\catroot2 catroot2.old', 15),
            ('Restarting services...', ' & '.join(f'net start {s}' for s in wu_services), 30),
        ]
        self.runRepairSteps(steps, 'Quick Reset Complete', 'Windows Update components have been reset.')

    def _reregisterUpdateDllsStep(self, output_emit):
        dlls = [
            "atl.dll", "urlmon.dll", "mshtml.dll", "shdocvw.dll", "browseui.dll",
            "jscript.dll", "vbscript.dll", "scrrun.dll", "msxml.dll", "msxml3.dll",
            "msxml6.dll", "actxprxy.dll", "softpub.dll", "wintrust.dll", "dssenh.dll",
            "rsaenh.dll", "gpkcsp.dll", "sccbase.dll", "slbcsp.dll", "cryptdlg.dll",
            "oleaut32.dll", "ole32.dll", "shell32.dll", "initpki.dll", "wuapi.dll",
            "wuaueng.dll", "wuaueng1.dll", "wucltui.dll", "wups.dll", "wups2.dll",
            "wuweb.dll", "qmgr.dll", "qmgrprxy.dll", "wucltux.dll", "muweb.dll", "wuwebv.dll"
        ]
        output_emit(f'Re-registering {len(dlls)} Windows Update DLLs...')
        success = 0
        for dll in dlls:
            try:
                result = subprocess.run(
                    f'regsvr32.exe /s {dll}', shell=True, capture_output=True, timeout=5,
                    creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0
                )
                if result.returncode == 0:
                    success += 1
            except Exception:
                pass
        output_emit(f'Re-registered {success}/{len(dlls)} DLLs')

    def reregisterUpdateDLLs(self):
        """Re-register the Windows Update DLLs only"""
        if not self.is_admin:
            QMessageBox.warning(self, 'Admin Required', 'This requires Administrator rights.')
            return
        self.runRepairSteps([self._reregisterUpdateDllsStep], 'DLL Re-registration Complete',
                             'Windows Update DLLs have been re-registered.')

    def windowsUpdateCompleteRepair(self):
        """Full Windows Update repair: cache, registry, DLLs, policies, services"""
        if not self.is_admin:
            QMessageBox.warning(self, 'Admin Required', 'This repair requires Administrator rights.')
            return
        reply = QMessageBox.question(
            self, 'Confirm Windows Update Repair',
            'This will perform a complete Windows Update repair:\n\n'
            '- Stop Windows Update services\n'
            '- Clear update cache and folders\n'
            '- Fix registry entries\n'
            '- Re-register all update DLLs\n'
            '- Restart services\n\n'
            'This may take 5-10 minutes. Continue?',
            QMessageBox.Yes | QMessageBox.No
        )
        if reply != QMessageBox.Yes:
            return

        wu_services = ['wuauserv', 'cryptSvc', 'bits', 'msiserver']
        reg_fixes = ' & '.join([
            'reg delete "HKLM\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\WindowsUpdate" /v AccountDomainSid /f',
            'reg delete "HKLM\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\WindowsUpdate" /v PingID /f',
            'reg delete "HKLM\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\WindowsUpdate" /v SusClientId /f',
            'reg delete "HKLM\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\WindowsUpdate" /v SusClientIDValidation /f',
            'reg add "HKLM\\SOFTWARE\\Policies\\Microsoft\\Windows\\WindowsUpdate" /v DisableWindowsUpdateAccess /t REG_DWORD /d 0 /f',
            'reg add "HKLM\\SOFTWARE\\Policies\\Microsoft\\Windows\\WindowsUpdate\\AU" /v NoAutoUpdate /t REG_DWORD /d 0 /f',
        ])
        clear_cache = ' & '.join([
            'takeown /f "C:\\Windows\\SoftwareDistribution" /r /d y',
            'icacls "C:\\Windows\\SoftwareDistribution" /grant administrators:F /t',
            'rd /s /q "C:\\Windows\\SoftwareDistribution"',
            'takeown /f "C:\\Windows\\System32\\catroot2" /r /d y',
            'icacls "C:\\Windows\\System32\\catroot2" /grant administrators:F /t',
            'rd /s /q "C:\\Windows\\System32\\catroot2"',
        ])

        steps = [
            ('Step 1/7: Stopping and disabling Windows Update services...',
             ' & '.join(f'net stop {s}' for s in wu_services) + ' & ' +
             ' & '.join(f'sc config {s} start= disabled' for s in wu_services), 30),
            ('Step 2/7: Clearing update cache (this can take a minute)...', clear_cache, 60),
            ('Step 3/7: Fixing registry entries...', reg_fixes, 15),
            self._reregisterUpdateDllsStep,
            ('Step 5/7: Resetting Windows Update policies...', 'gpupdate /force & bitsadmin /reset /allusers', 40),
            ('Step 6/7: Re-enabling and restarting services...',
             ' & '.join(f'sc config {s} start= auto' for s in wu_services) + ' & ' +
             ' & '.join(f'net start {s}' for s in wu_services), 30),
            ('Step 7/7: Forcing a Windows Update check...',
             'powershell -NoProfile -Command "UsoClient StartScan" & wuauclt /detectnow', 15),
        ]
        self.runRepairSteps(steps, 'Windows Update Repair Complete',
                             'Windows Update has been completely repaired.\n\nRestart recommended for best results.')

    def defenderReenable(self):
        """Re-enable Windows Defender if it's been disabled"""
        if not self.is_admin:
            QMessageBox.warning(self, 'Admin Required', 'This requires Administrator rights.')
            return
        reg_cmd = ' & '.join([
            'reg delete "HKLM\\SOFTWARE\\Policies\\Microsoft\\Windows Defender" /v DisableAntiSpyware /f',
            'reg delete "HKLM\\SOFTWARE\\Policies\\Microsoft\\Windows Defender" /v DisableAntiVirus /f',
            'reg add "HKLM\\SOFTWARE\\Microsoft\\Windows Defender" /v DisableAntiSpyware /t REG_DWORD /d 0 /f',
        ])
        steps = [
            ('Re-enabling Windows Defender...', reg_cmd, 10),
            ('Starting Defender service...', 'sc config WinDefend start= auto & net start WinDefend', 15),
            ('Enabling real-time protection...',
             'powershell -NoProfile -Command "Set-MpPreference -DisableRealtimeMonitoring $false"', 10),
        ]
        self.runRepairSteps(steps, 'Defender Re-enabled', 'Windows Defender has been re-enabled.')

    def defenderResetDefaults(self):
        """Reset Windows Defender settings to their defaults"""
        if not self.is_admin:
            QMessageBox.warning(self, 'Admin Required', 'This requires Administrator rights.')
            return
        ps = ';'.join([
            'Set-MpPreference -DisableRealtimeMonitoring $false',
            'Set-MpPreference -SubmitSamplesConsent 1',
            'Set-MpPreference -MAPSReporting 2',
            'Set-MpPreference -HighThreatDefaultAction Quarantine',
            'Set-MpPreference -ModerateThreatDefaultAction Quarantine',
            'Update-MpSignature',
        ])
        steps = [('Resetting Defender to defaults...', f'powershell -NoProfile -Command "{ps}"', 60)]
        self.runRepairSteps(steps, 'Defender Reset', 'Windows Defender has been reset to default settings.')

    def defenderCompleteRepair(self):
        """Full Defender repair: re-enable, reset settings, update definitions, scan"""
        if not self.is_admin:
            QMessageBox.warning(self, 'Admin Required', 'This repair requires Administrator rights.')
            return
        reply = QMessageBox.question(
            self, 'Confirm Defender Repair',
            'This will completely reset Windows Defender:\n\n'
            '- Re-enable if disabled\n'
            '- Reset all settings to default\n'
            '- Update definitions\n'
            '- Restart protection\n\n'
            'Continue?',
            QMessageBox.Yes | QMessageBox.No
        )
        if reply != QMessageBox.Yes:
            return

        reg_enable = ' & '.join([
            'reg delete "HKLM\\SOFTWARE\\Policies\\Microsoft\\Windows Defender" /v DisableAntiSpyware /f',
            'reg delete "HKLM\\SOFTWARE\\Policies\\Microsoft\\Windows Defender" /v DisableAntiVirus /f',
            'reg delete "HKLM\\SOFTWARE\\Policies\\Microsoft\\Windows Defender\\Real-Time Protection" /v DisableBehaviorMonitoring /f',
            'reg delete "HKLM\\SOFTWARE\\Policies\\Microsoft\\Windows Defender\\Real-Time Protection" /v DisableOnAccessProtection /f',
            'reg delete "HKLM\\SOFTWARE\\Policies\\Microsoft\\Windows Defender\\Real-Time Protection" /v DisableRealtimeMonitoring /f',
            'reg delete "HKLM\\SOFTWARE\\Policies\\Microsoft\\Windows Defender\\Real-Time Protection" /v DisableScanOnRealtimeEnable /f',
            'reg add "HKLM\\SOFTWARE\\Microsoft\\Windows Defender" /v DisableAntiSpyware /t REG_DWORD /d 0 /f',
            'reg add "HKLM\\SOFTWARE\\Microsoft\\Windows Defender" /v DisableAntiVirus /t REG_DWORD /d 0 /f',
            'reg add "HKLM\\SOFTWARE\\Microsoft\\Windows Defender\\Real-Time Protection" /v DisableBehaviorMonitoring /t REG_DWORD /d 0 /f',
            'reg add "HKLM\\SOFTWARE\\Microsoft\\Windows Defender\\Real-Time Protection" /v DisableIOAVProtection /t REG_DWORD /d 0 /f',
            'reg add "HKLM\\SOFTWARE\\Microsoft\\Windows Defender\\Real-Time Protection" /v DisableOnAccessProtection /t REG_DWORD /d 0 /f',
            'reg add "HKLM\\SOFTWARE\\Microsoft\\Windows Defender\\Real-Time Protection" /v DisableRealtimeMonitoring /t REG_DWORD /d 0 /f',
            'reg add "HKLM\\SOFTWARE\\Microsoft\\Windows Defender\\Real-Time Protection" /v DisableScanOnRealtimeEnable /t REG_DWORD /d 0 /f',
        ])
        services_cmd = ' & '.join(f'sc config {s} start= auto & net start {s}' for s in ['WinDefend', 'WdNisSvc', 'Sense', 'wscsvc'])
        ps_resets = ';'.join([
            'Set-MpPreference -DisableRealtimeMonitoring $false',
            'Set-MpPreference -DisableBehaviorMonitoring $false',
            'Set-MpPreference -DisableBlockAtFirstSeen $false',
            'Set-MpPreference -DisableIOAVProtection $false',
            'Set-MpPreference -DisablePrivacyMode $false',
            'Set-MpPreference -SignatureDisableUpdateOnStartupWithoutEngine $false',
            'Set-MpPreference -DisableArchiveScanning $false',
            'Set-MpPreference -DisableIntrusionPreventionSystem $false',
            'Set-MpPreference -DisableScriptScanning $false',
            'Set-MpPreference -SubmitSamplesConsent 1',
            'Set-MpPreference -MAPSReporting 2',
            'Set-MpPreference -HighThreatDefaultAction Quarantine',
            'Set-MpPreference -ModerateThreatDefaultAction Quarantine',
            'Set-MpPreference -LowThreatDefaultAction Quarantine',
            'Set-MpPreference -SevereThreatDefaultAction Quarantine',
        ])

        steps = [
            ('Step 1/5: Re-enabling Windows Defender (registry)...', reg_enable, 15),
            ('Step 2/5: Enabling and starting Defender services...', services_cmd, 30),
            ('Step 3/5: Resetting Defender settings...', f'powershell -NoProfile -Command "{ps_resets}"', 30),
            ('Step 4/5: Updating virus definitions...', 'powershell -NoProfile -Command "Update-MpSignature"', 90),
            ('Step 5/5: Running a quick scan...', 'powershell -NoProfile -Command "Start-MpScan -ScanType QuickScan"', 10),
        ]
        self.runRepairSteps(steps, 'Defender Repair Complete',
                             'Windows Defender has been completely repaired.\n\nCheck Windows Security to verify.')

    def quickHealthCheck(self):
        """Safe, read-only diagnostic sweep"""
        checks = [
            ('Checking disk health (SMART)...',
             'powershell -NoProfile -Command "Get-PhysicalDisk | Select-Object FriendlyName, HealthStatus | Format-Table -AutoSize"', 15),
            ('Checking memory modules...',
             'powershell -NoProfile -Command "Get-CimInstance Win32_PhysicalMemory | Select-Object Capacity, Speed, Manufacturer | Format-Table -AutoSize"', 15),
            ('Verifying system files (sfc /verifyonly)...', 'sfc /verifyonly', 120),
            ('Testing network connectivity...', 'ping -n 3 8.8.8.8', 15),
            ('Checking Windows version...',
             'powershell -NoProfile -Command "Get-CimInstance Win32_OperatingSystem | Select-Object Caption, Version | Format-List"', 15),
            ('Listing recent critical/error events...',
             'wevtutil qe System /c:10 /rd:true /f:text /q:"*[System[(Level=1 or Level=2)]]"', 15),
        ]
        self.runRepairSteps(checks, 'Health Check Complete', 'Quick health check finished - check the log below for details.')

    def autoRepairSFCDISM(self):
        """Run SFC and DISM RestoreHealth to repair corrupted system files"""
        if not self.is_admin:
            QMessageBox.warning(self, 'Admin Required', 'Auto-repair requires Administrator rights.')
            return
        reply = QMessageBox.question(
            self, 'Confirm', 'This will run SFC and DISM repairs.\n\nThis may take 15-30 minutes.\n\nContinue?',
            QMessageBox.Yes | QMessageBox.No
        )
        if reply != QMessageBox.Yes:
            return
        steps = [
            ('Running System File Checker (sfc /scannow)...', 'sfc /scannow', 1200),
            ('Running DISM /RestoreHealth...', 'DISM /Online /Cleanup-Image /RestoreHealth', 1800),
        ]
        self.runRepairSteps(steps, 'Repair Complete',
                             'SFC and DISM repair finished.\n\nRestart your PC for changes to take full effect.')

    def createSystemRestorePoint(self):
        """Create a System Restore checkpoint"""
        if not self.is_admin:
            QMessageBox.warning(self, 'Admin Required', 'Creating a restore point requires Administrator rights.')
            return
        steps = [(
            'Creating system restore point...',
            'powershell -NoProfile -Command "Checkpoint-Computer -Description \'REGwintool_Manual\' -RestorePointType \'MODIFY_SETTINGS\'"', 60
        )]
        self.runRepairSteps(steps, 'Restore Point Created', 'A system restore point has been created.')

    def checkDiskScan(self):
        """Schedule a chkdsk error scan for the next restart"""
        if not self.is_admin:
            QMessageBox.warning(self, 'Admin Required', 'Checking a disk requires Administrator rights.')
            return
        drive, ok = QInputDialog.getText(self, 'Check Disk', 'Drive letter to check (e.g. C):', text='C')
        if not ok or not drive.strip():
            return
        drive = drive.strip().upper().rstrip(':')
        if len(drive) != 1 or not drive.isalpha():
            QMessageBox.warning(self, 'Invalid Drive', 'Enter a single drive letter, e.g. C')
            return
        reply = QMessageBox.question(
            self, 'Confirm', f'Schedule a disk check for drive {drive}: on next restart?',
            QMessageBox.Yes | QMessageBox.No
        )
        if reply != QMessageBox.Yes:
            return
        steps = [(f'Scheduling chkdsk for {drive}:...', f'echo Y | chkdsk {drive}: /F /R', 15)]
        self.runRepairSteps(steps, 'Disk Check Scheduled',
                             f'A disk check for drive {drive}: has been scheduled for the next restart.')

    # ==================== PRIVACY FUNCTIONS ====================
    
    def clearBrowserData(self, browser):
        """Clear specific browser data"""
        user_profile = os.environ.get('USERPROFILE')
        
        paths = {
            'Chrome': [
                os.path.join(user_profile, 'AppData', 'Local', 'Google', 'Chrome', 'User Data', 'Default', 'Cache'),
                os.path.join(user_profile, 'AppData', 'Local', 'Google', 'Chrome', 'User Data', 'Default', 'Code Cache'),
            ],
            'Edge': [
                os.path.join(user_profile, 'AppData', 'Local', 'Microsoft', 'Edge', 'User Data', 'Default', 'Cache'),
                os.path.join(user_profile, 'AppData', 'Local', 'Microsoft', 'Edge', 'User Data', 'Default', 'Code Cache'),
            ]
        }

        if browser == 'Firefox':
            # Only the cache subfolders inside each profile - never the profile
            # folder itself, which holds bookmarks/passwords/history.
            browser_paths = []
            profiles_root = os.path.join(user_profile, 'AppData', 'Local', 'Mozilla', 'Firefox', 'Profiles')
            if os.path.isdir(profiles_root):
                for entry in os.scandir(profiles_root):
                    if entry.is_dir(follow_symlinks=False):
                        for cache_name in ('cache2', 'startupCache', 'shader-cache', 'OfflineCache'):
                            browser_paths.append(os.path.join(entry.path, cache_name))
        else:
            browser_paths = paths.get(browser, [])
        cleaned = 0
        
        for path in browser_paths:
            if os.path.exists(path):
                try:
                    for entry in os.scandir(path):
                        try:
                            if entry.is_file(follow_symlinks=False):
                                os.remove(entry.path)
                                cleaned += 1
                            elif entry.is_dir(follow_symlinks=False):
                                shutil.rmtree(entry.path)
                                cleaned += 1
                        except Exception:
                            pass
                except Exception:
                    pass
        
        msg = f'{browser} cache cleared ({cleaned} items)'
        self.privacy_log.append(f'[{datetime.now().strftime("%H:%M:%S")}] {msg}')
        self.logActivity(f'Privacy: {msg}')
        QMessageBox.information(self, 'Success', msg)
    
    def clearAllBrowsers(self):
        """Clear all browser caches"""
        self.clearBrowserData('Chrome')
        self.clearBrowserData('Firefox')
        self.clearBrowserData('Edge')
    
    def clearTempFiles(self):
        """Clear temporary files"""
        temp_folder = os.environ.get('TEMP')
        
        if temp_folder and os.path.exists(temp_folder):
            count = 0
            size = 0
            
            for entry in os.scandir(temp_folder):
                try:
                    if entry.is_file(follow_symlinks=False):
                        size += entry.stat().st_size
                        os.remove(entry.path)
                        count += 1
                    elif entry.is_dir(follow_symlinks=False):
                        folder_size = self.getFolderSize(entry.path)
                        size += folder_size
                        shutil.rmtree(entry.path)
                        count += 1
                except Exception:
                    pass
            
            size_mb = size / (1024 * 1024)
            msg = f'Temp files cleared: {count} items, {size_mb:.2f} MB'
            self.privacy_log.append(f'[{datetime.now().strftime("%H:%M:%S")}] {msg}')
            self.logActivity(f'Privacy: {msg}')
            QMessageBox.information(self, 'Success', msg)
    
    def clearRecentFiles(self):
        """Clear recent files list"""
        try:
            recent = os.path.join(os.environ.get('APPDATA'), 'Microsoft', 'Windows', 'Recent')
            
            if os.path.exists(recent):
                count = 0
                for entry in os.scandir(recent):
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            # AutomaticDestinations / CustomDestinations (jump lists)
                            shutil.rmtree(entry.path)
                        else:
                            os.remove(entry.path)
                        count += 1
                    except Exception:
                        pass

                msg = f'Recent files cleared: {count} items'
                self.privacy_log.append(f'[{datetime.now().strftime("%H:%M:%S")}] {msg}')
                self.logActivity(f'Privacy: {msg}')
                QMessageBox.information(self, 'Success', msg)
        except Exception as e:
            QMessageBox.warning(self, 'Error', f'Could not clear recent files: {str(e)}')
    
    def clearClipboard(self):
        """Clear clipboard"""
        try:
            clipboard = QApplication.clipboard()
            clipboard.clear()
            
            msg = 'Clipboard cleared'
            self.privacy_log.append(f'[{datetime.now().strftime("%H:%M:%S")}] {msg}')
            self.logActivity(f'Privacy: {msg}')
            QMessageBox.information(self, 'Success', msg)
        except Exception:
            QMessageBox.warning(self, 'Error', 'Could not clear clipboard')
    
    def flushDNS(self):
        """Flush DNS cache"""
        try:
            result = subprocess.run(['ipconfig', '/flushdns'], capture_output=True, text=True)
            
            msg = 'DNS cache flushed'
            self.privacy_log.append(f'[{datetime.now().strftime("%H:%M:%S")}] {msg}')
            self.logActivity(f'Privacy: {msg}')
            QMessageBox.information(self, 'Success', msg)
        except Exception:
            QMessageBox.warning(self, 'Error', 'Could not flush DNS cache')
    
    def clearPrefetch(self):
        """Clear prefetch files"""
        prefetch = os.path.join(os.environ.get('WINDIR', 'C:\\Windows'), 'Prefetch')
        
        if os.path.exists(prefetch):
            count = 0
            for entry in os.scandir(prefetch):
                try:
                    if entry.is_file():
                        os.remove(entry.path)
                        count += 1
                except Exception:
                    pass
            
            if count > 0:
                msg = f'Prefetch cleared: {count} files'
                self.privacy_log.append(f'[{datetime.now().strftime("%H:%M:%S")}] {msg}')
                self.logActivity(f'Privacy: {msg}')
                QMessageBox.information(self, 'Success', msg)
            else:
                QMessageBox.warning(self, 'Access Denied', 'Administrator rights required for prefetch cleanup')
        else:
            QMessageBox.warning(self, 'Not Found', 'Prefetch folder not found')
    
    # ==================== UNINSTALLER FUNCTIONS ====================
    
    def loadInstalledPrograms(self):
        """Load installed programs list"""
        self.status_bar.showMessage('Loading installed programs...')
        self.uninstall_table.setRowCount(0)
        
        programs = []
        
        paths = [
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
            (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
        ]
        
        for hive, path in paths:
            try:
                key = winreg.OpenKey(hive, path, 0, winreg.KEY_READ)
                
                for i in range(min(200, winreg.QueryInfoKey(key)[0])):
                    try:
                        subkey_name = winreg.EnumKey(key, i)
                        subkey = winreg.OpenKey(key, subkey_name, 0, winreg.KEY_READ)
                        
                        try:
                            display_name = winreg.QueryValueEx(subkey, "DisplayName")[0]
                            publisher = ""
                            version = ""
                            install_date = ""
                            
                            try:
                                publisher = winreg.QueryValueEx(subkey, "Publisher")[0]
                            except Exception:
                                pass
                            
                            try:
                                version = winreg.QueryValueEx(subkey, "DisplayVersion")[0]
                            except Exception:
                                pass
                            
                            try:
                                install_date = winreg.QueryValueEx(subkey, "InstallDate")[0]
                            except Exception:
                                pass

                            uninstall_string = ""
                            try:
                                uninstall_string = winreg.QueryValueEx(subkey, "QuietUninstallString")[0]
                            except Exception:
                                try:
                                    uninstall_string = winreg.QueryValueEx(subkey, "UninstallString")[0]
                                except Exception:
                                    pass

                            system_component = 0
                            try:
                                system_component = winreg.QueryValueEx(subkey, "SystemComponent")[0]
                            except Exception:
                                pass

                            if system_component and self.settings.get('skip_system_components', True):
                                # Hidden OS/runtime component - Programs and Features
                                # hides these too; skip so bulk-uninstall can't touch them.
                                pass
                            else:
                                programs.append({
                                    'name': display_name,
                                    'publisher': publisher,
                                    'version': version,
                                    'install_date': install_date,
                                    'uninstall_string': uninstall_string
                                })
                        except Exception:
                            pass
                        
                        winreg.CloseKey(subkey)
                    except Exception:
                        continue
                
                winreg.CloseKey(key)
            except Exception:
                pass
        
        # Remove duplicates
        seen = set()
        unique = []
        for prog in programs:
            if prog['name'] not in seen:
                seen.add(prog['name'])
                unique.append(prog)
        
        # Sort alphabetically
        unique.sort(key=lambda x: x['name'].lower())
        self.installed_programs = unique

        # Display
        self.uninstall_table.setRowCount(len(unique))

        for row, prog in enumerate(unique):
            checkbox = QCheckBox()
            checkbox.setChecked(False)
            checkbox_widget = QWidget()
            checkbox_layout = QHBoxLayout(checkbox_widget)
            checkbox_layout.addWidget(checkbox)
            checkbox_layout.setAlignment(Qt.AlignCenter)
            checkbox_layout.setContentsMargins(0, 0, 0, 0)
            self.uninstall_table.setCellWidget(row, 0, checkbox_widget)

            self.uninstall_table.setItem(row, 1, QTableWidgetItem(prog['name']))
            self.uninstall_table.setItem(row, 2, QTableWidgetItem(prog['publisher']))
            self.uninstall_table.setItem(row, 3, QTableWidgetItem(prog['version']))
            self.uninstall_table.setItem(row, 4, QTableWidgetItem(prog['install_date']))

        self.uninstall_table.resizeColumnsToContents()
        self.status_bar.showMessage(f'Loaded {len(unique)} programs')
        self.logActivity(f'Programs: {len(unique)} listed')
    
    # ==================== BACKUP FUNCTIONS ====================
    
    def createFullBackup(self):
        """Create full system backup"""
        try:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_file = os.path.join(self.backup_dir, f'full_backup_{timestamp}.json')
            
            self.status_bar.showMessage('Creating backup...')
            self.progress_bar.setVisible(True)
            self.progress_bar.setValue(0)
            
            backup_data = {
                'timestamp': timestamp,
                'datetime': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                'version': '2.1',
                'backup_type': 'full',
                'admin_mode': self.is_admin,
                'startup_items': [],
                'registry_snapshot': {},
                'system_info': {}
            }
            
            # Backup startup items
            self.progress_bar.setValue(30)
            startup_items = []
            paths = [
                (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Run", "HKCU"),
                (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Run", "HKLM"),
            ]
            
            for hive, path, location in paths:
                try:
                    key = winreg.OpenKey(hive, path, 0, winreg.KEY_READ)
                    
                    for i in range(winreg.QueryInfoKey(key)[1]):
                        try:
                            name, value, vtype = winreg.EnumValue(key, i)
                            startup_items.append({
                                'name': name,
                                'value': value,
                                'location': location,
                                'path': path
                            })
                        except Exception:
                            continue
                    
                    winreg.CloseKey(key)
                except Exception:
                    pass
            
            backup_data['startup_items'] = startup_items

            # Registry snapshot - current values of the Run keys we manage,
            # keyed by full path so a restore can tell exactly what changed
            registry_snapshot = {}
            for item in startup_items:
                snap_key = f"{item['location']}\\{item['path']}"
                registry_snapshot.setdefault(snap_key, {})[item['name']] = item['value']
            backup_data['registry_snapshot'] = registry_snapshot

            # System info
            self.progress_bar.setValue(60)
            try:
                import platform
                backup_data['system_info'] = {
                    'os': platform.system(),
                    'release': platform.release(),
                    'version': platform.version(),
                    'machine': platform.machine(),
                    'node': platform.node(),
                    'python': platform.python_version()
                }
            except Exception:
                pass
            
            # Save
            self.progress_bar.setValue(90)
            with open(backup_file, 'w', encoding='utf-8') as f:
                json.dump(backup_data, f, indent=2, ensure_ascii=False)
            
            self.progress_bar.setValue(100)
            self.progress_bar.setVisible(False)
            
            # Verify
            if os.path.exists(backup_file):
                file_size = os.path.getsize(backup_file) / 1024
                
                QMessageBox.information(
                    self,
                    'Backup Created',
                    f'Backup saved successfully!\n\n'
                    f'File: {os.path.basename(backup_file)}\n'
                    f'Size: {file_size:.2f} KB\n'
                    f'Startup items: {len(startup_items)}\n'
                    f'Date: {backup_data["datetime"]}'
                )
                
                self.logActivity(f'Backup created: {file_size:.2f} KB')
                self.status_bar.showMessage('Backup created successfully')
                
                # Refresh backup list
                self.loadBackupList()
                
                # Offer to open folder
                reply = QMessageBox.question(
                    self,
                    'Open Folder?',
                    'Open backup folder to view the file?',
                    QMessageBox.Yes | QMessageBox.No
                )
                
                if reply == QMessageBox.Yes:
                    os.startfile(self.backup_dir)
            else:
                QMessageBox.warning(self, 'Error', 'Backup file was not created!')
            
        except Exception as e:
            self.progress_bar.setVisible(False)
            QMessageBox.critical(self, 'Backup Error', f'Failed to create backup:\n{str(e)}')
            self.logActivity(f'Backup failed: {str(e)}')
    
    def createRegistryBackup(self):
        """Create registry backup before fixes"""
        try:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_file = os.path.join(self.backup_dir, f'registry_backup_{timestamp}.json')
            
            backup_data = {
                'timestamp': timestamp,
                'datetime': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                'backup_type': 'registry',
                'issues_count': len(self.scan_results),
                'issues': self.scan_results
            }
            
            with open(backup_file, 'w', encoding='utf-8') as f:
                json.dump(backup_data, f, indent=2, ensure_ascii=False)
            
            if os.path.exists(backup_file):
                self.logActivity(f'Registry backup: {os.path.basename(backup_file)}')
                return backup_file
            
            return None
            
        except Exception as e:
            print(f"Backup error: {e}")
            return None
    
    def loadBackupList(self):
        """Load list of available backups"""
        try:
            self.backups_table.setRowCount(0)
            
            if not os.path.exists(self.backup_dir):
                return
            
            backups = []
            
            for filename in os.listdir(self.backup_dir):
                if filename.endswith('.json'):
                    filepath = os.path.join(self.backup_dir, filename)
                    
                    try:
                        with open(filepath, 'r', encoding='utf-8') as f:
                            data = json.load(f)
                        
                        size = os.path.getsize(filepath) / 1024
                        backup_type = data.get('backup_type', 'unknown')
                        date_time = data.get('datetime', 'unknown')
                        
                        items_count = 0
                        if backup_type == 'full':
                            items_count = len(data.get('startup_items', []))
                        elif backup_type == 'registry':
                            items_count = data.get('issues_count', 0)
                        
                        backups.append({
                            'filename': filename,
                            'date': date_time,
                            'type': backup_type,
                            'size': f'{size:.2f} KB',
                            'items': str(items_count),
                            'filepath': filepath
                        })
                    except Exception:
                        continue
            
            # Sort by date (newest first)
            backups.sort(key=lambda x: x['date'], reverse=True)
            
            # Display
            self.backups_table.setRowCount(len(backups))
            
            for row, backup in enumerate(backups):
                self.backups_table.setItem(row, 0, QTableWidgetItem(backup['filename']))
                self.backups_table.setItem(row, 1, QTableWidgetItem(backup['date']))
                self.backups_table.setItem(row, 2, QTableWidgetItem(backup['type']))
                self.backups_table.setItem(row, 3, QTableWidgetItem(backup['size']))
                self.backups_table.setItem(row, 4, QTableWidgetItem(backup['items']))
            
            self.backups_table.resizeColumnsToContents()
            self.status_bar.showMessage(f'Found {len(backups)} backups')
            
        except Exception as e:
            print(f"Error loading backups: {e}")
    
    def viewSelectedBackup(self):
        """View selected backup contents"""
        selected = self.backups_table.currentRow()
        if selected < 0:
            QMessageBox.warning(self, 'No Selection', 'Please select a backup to view!')
            return
        
        filename = self.backups_table.item(selected, 0).text()
        filepath = os.path.join(self.backup_dir, filename)
        
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            # Create viewer dialog
            viewer = QMessageBox(self)
            viewer.setWindowTitle('Backup Contents')
            viewer.setIcon(QMessageBox.Information)
            
            contents = json.dumps(data, indent=2)
            viewer.setDetailedText(contents)
            
            viewer.setText(
                f"Backup: {filename}\n\n"
                f"Date: {data.get('datetime', 'unknown')}\n"
                f"Type: {data.get('backup_type', 'unknown')}\n\n"
                "Click 'Show Details' to view full JSON data"
            )
            
            viewer.exec_()
            
        except Exception as e:
            QMessageBox.critical(self, 'Error', f'Failed to view backup:\n{str(e)}')
    
    def deleteSelectedBackup(self):
        """Delete selected backup"""
        selected = self.backups_table.currentRow()
        if selected < 0:
            QMessageBox.warning(self, 'No Selection', 'Please select a backup to delete!')
            return
        
        filename = self.backups_table.item(selected, 0).text()
        filepath = os.path.join(self.backup_dir, filename)
        
        reply = QMessageBox.question(
            self,
            'Confirm Deletion',
            f'Delete backup file?\n\n{filename}\n\nThis cannot be undone!',
            QMessageBox.Yes | QMessageBox.No
        )
        
        if reply == QMessageBox.Yes:
            try:
                os.remove(filepath)
                QMessageBox.information(self, 'Success', 'Backup deleted successfully')
                self.loadBackupList()
                self.logActivity(f'Backup deleted: {filename}')
            except Exception as e:
                QMessageBox.critical(self, 'Error', f'Failed to delete:\n{str(e)}')
    
    def changeBackupDir(self):
        """Change backup directory"""
        new_dir = QFileDialog.getExistingDirectory(
            self,
            'Select Backup Directory',
            self.backup_dir
        )
        
        if new_dir:
            self.backup_dir = new_dir
            self.backup_path_label.setText(new_dir)
            self.backup_location_label.setText(new_dir)
            self.settings['backup_dir'] = new_dir
            save_settings(self.settings)
            QMessageBox.information(self, 'Success', f'Backup location changed to:\n{new_dir}')
            self.logActivity(f'Backup location: {new_dir}')
            self.loadBackupList()

    # ---- File Backup (mirror) ----

    def addFileBackupJob(self):
        """Pick a source folder and a destination, and add a mirror backup job"""
        source = QFileDialog.getExistingDirectory(
            self, 'Select Folder To Back Up', os.environ.get('USERPROFILE', 'C:\\'))
        if not source:
            return
        source = os.path.normpath(source)

        dest_root = QFileDialog.getExistingDirectory(self, 'Select Backup Destination', self.backup_dir)
        if not dest_root:
            return
        destination = os.path.join(os.path.normpath(dest_root), os.path.basename(source))

        jobs = self.settings.get('file_backup_jobs', [])
        if any(j['source'] == source for j in jobs):
            QMessageBox.warning(self, 'Already Added', 'This folder is already in the backup list.')
            return

        jobs.append({'source': source, 'destination': destination, 'last_run': ''})
        self.settings['file_backup_jobs'] = jobs
        save_settings(self.settings)
        self.loadFileBackupJobs()
        self.logActivity(f'File backup job added: {source} -> {destination}')

    def removeFileBackupJob(self):
        """Remove the selected backup job (does not delete any files)"""
        row = self.file_backup_table.currentRow()
        jobs = self.settings.get('file_backup_jobs', [])
        if row < 0 or row >= len(jobs):
            QMessageBox.warning(self, 'No Selection', 'Select a backup job to remove!')
            return

        removed = jobs.pop(row)
        self.settings['file_backup_jobs'] = jobs
        save_settings(self.settings)
        self.loadFileBackupJobs()
        self.logActivity(f"File backup job removed: {removed.get('source', '')}")

    def loadFileBackupJobs(self):
        """Populate the file backup jobs table from settings"""
        jobs = self.settings.get('file_backup_jobs', [])
        self.file_backup_table.setRowCount(len(jobs))
        for row, job in enumerate(jobs):
            self.file_backup_table.setItem(row, 0, QTableWidgetItem(job.get('source', '')))
            self.file_backup_table.setItem(row, 1, QTableWidgetItem(job.get('destination', '')))
            self.file_backup_table.setItem(row, 2, QTableWidgetItem(job.get('last_run') or 'Never'))
        self.file_backup_table.resizeColumnsToContents()

    def testBackupDestination(self):
        """Check whether the selected job's destination is reachable - pings
        the host for a network (UNC) path, checks the parent folder otherwise."""
        row = self.file_backup_table.currentRow()
        jobs = self.settings.get('file_backup_jobs', [])
        if row < 0 or row >= len(jobs):
            QMessageBox.warning(self, 'No Selection', 'Select a backup job to test!')
            return

        destination = jobs[row]['destination']
        if destination.startswith('\\\\'):
            host = destination.strip('\\').split('\\')[0]
            try:
                result = subprocess.run(
                    ['ping', '-n', '1', '-w', '1000', host],
                    capture_output=True, text=True, timeout=3,
                    creationflags=subprocess.CREATE_NO_WINDOW
                )
                reachable = result.returncode == 0
            except Exception:
                reachable = False
            if reachable:
                QMessageBox.information(self, 'Reachable', f'{host} is reachable.')
            else:
                QMessageBox.warning(self, 'Not Reachable',
                    f'{host} could not be reached. Check the network path and your credentials '
                    '(a mapped drive letter or saved login may not carry over when running as Administrator).')
        else:
            parent = os.path.dirname(destination.rstrip('\\/')) or destination
            if os.path.exists(parent):
                QMessageBox.information(self, 'Local Path', 'The destination\'s parent folder exists and is reachable.')
            else:
                QMessageBox.warning(self, 'Not Found', f"Parent folder doesn't exist yet:\n{parent}\n\nIt will be created on first backup, or check the path is correct.")

    def runFileBackup(self):
        """Mirror every configured source folder into its backup destination"""
        if self.file_mirror_thread is not None and self.file_mirror_thread.isRunning():
            QMessageBox.information(self, 'Busy', 'A file backup is already running.')
            return

        jobs = self.settings.get('file_backup_jobs', [])
        if not jobs:
            QMessageBox.warning(self, 'No Backup Jobs', "Click 'Add Folder...' to set up a backup first.")
            return

        missing = [j for j in jobs if not os.path.isdir(j['source'])]
        valid_jobs = [j for j in jobs if os.path.isdir(j['source'])]
        if missing:
            names = '\n'.join(f"- {j['source']}" for j in missing)
            QMessageBox.warning(self, 'Source Missing',
                f'These source folders no longer exist and will be skipped:\n\n{names}')
        if not valid_jobs:
            return

        preview = '\n'.join(f"- {j['source']}\n    -> {j['destination']}" for j in valid_jobs)
        reply = QMessageBox.question(
            self, 'Run File Backup',
            f'Mirror {len(valid_jobs)} folder(s)?\n\n{preview}\n\n'
            'Each destination will become an EXACT copy of its source - files removed '
            'from the source since the last backup will also be removed from the destination.',
            QMessageBox.Yes | QMessageBox.No
        )
        if reply != QMessageBox.Yes:
            return

        self.setBackupButtonsEnabled(False)
        self.appendBackupLog(f'\n=== File Backup - starting ({len(valid_jobs)} folder(s)) ===')
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(0)
        pairs = [(j['source'], j['destination']) for j in valid_jobs]
        self.file_mirror_thread = FileMirrorThread(pairs)
        self.file_mirror_thread.progress.connect(self.onFileMirrorProgress)
        self.file_mirror_thread.finished.connect(lambda r: self.displayFileBackupResults(r, is_restore=False))
        self.file_mirror_thread.start()
        self.file_backup_cancel_btn.setEnabled(True)

    def restoreSelectedFileBackup(self):
        """Reverse-mirror the selected job's backup back onto its original source"""
        if self.file_mirror_thread is not None and self.file_mirror_thread.isRunning():
            QMessageBox.information(self, 'Busy', 'A file operation is already running.')
            return

        row = self.file_backup_table.currentRow()
        jobs = self.settings.get('file_backup_jobs', [])
        if row < 0 or row >= len(jobs):
            QMessageBox.warning(self, 'No Selection', 'Select a backup job to restore!')
            return

        job = jobs[row]
        if not os.path.isdir(job['destination']):
            QMessageBox.warning(self, 'No Backup Found', f"No backup exists yet at:\n{job['destination']}")
            return

        reply = QMessageBox.warning(
            self, 'Confirm Restore - This Deletes Files',
            f"This will make:\n\n{job['source']}\n\nan EXACT COPY of the backup:\n\n{job['destination']}\n\n"
            'Any files in the ORIGINAL folder that are not in the backup will be PERMANENTLY DELETED. '
            'This cannot be undone. Continue?',
            QMessageBox.Yes | QMessageBox.No
        )
        if reply != QMessageBox.Yes:
            return

        self.setBackupButtonsEnabled(False)
        self.appendBackupLog(f"\n=== Restoring {job['source']} - starting ===")
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(0)
        self.file_mirror_thread = FileMirrorThread([(job['destination'], job['source'])])
        self.file_mirror_thread.progress.connect(self.onFileMirrorProgress)
        self.file_mirror_thread.finished.connect(lambda r: self.displayFileBackupResults(r, is_restore=True))
        self.file_mirror_thread.start()
        self.file_backup_cancel_btn.setEnabled(True)

    def cancelFileBackup(self):
        """Request cancellation of the running file backup/restore"""
        if self.file_mirror_thread is not None and self.file_mirror_thread.isRunning():
            self.file_mirror_thread.cancel()
            self.status_bar.showMessage('Cancelling...', 3000)
            self.appendBackupLog('Cancelling...')

    def onFileMirrorProgress(self, message, percent):
        """FileMirrorThread progress -> both the global progress bar and the visible backup log"""
        self.updateProgress(message, percent)
        self.appendBackupLog(message)

    def displayFileBackupResults(self, payload, is_restore=False):
        """Show the outcome of a file backup/restore run and update last-run times"""
        self.setBackupButtonsEnabled(True)
        self.progress_bar.setVisible(False)
        self.file_backup_cancel_btn.setEnabled(False)
        results = payload.get('results', [])
        verb = 'Restore' if is_restore else 'Backup'

        if payload.get('cancelled'):
            self.appendBackupLog(f'=== {verb} cancelled ===\n')
            QMessageBox.information(self, f'{verb} Cancelled', f'{verb} was cancelled partway through.')
            self.logActivity(f'File {verb.lower()}: cancelled')
            return

        succeeded = [r for r in results if r['success']]
        failed = [r for r in results if not r['success']]

        msg = f"{verb} complete.\nSucceeded: {len(succeeded)} / {len(results)}"
        if failed:
            msg += '\n\nFailed:\n' + '\n'.join(f"- {r['source']}: {r['error']}" for r in failed[:10])
        self.appendBackupLog(f'=== {verb} complete: {len(succeeded)}/{len(results)} succeeded ===\n')
        QMessageBox.information(self, f'{verb} Complete', msg)
        self.logActivity(f'File {verb.lower()}: {len(succeeded)} succeeded, {len(failed)} failed')

        if not is_restore:
            jobs = self.settings.get('file_backup_jobs', [])
            now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            succeeded_sources = {r['source'] for r in succeeded}
            for job in jobs:
                if job['source'] in succeeded_sources:
                    job['last_run'] = now
            self.settings['file_backup_jobs'] = jobs
            save_settings(self.settings)
            self.loadFileBackupJobs()

    def quickAddCommonFolders(self):
        """Add Desktop, Documents, and Pictures as file backup jobs in one click"""
        user_profile = os.environ.get('USERPROFILE', '')
        candidates = {
            'Desktop': os.path.join(user_profile, 'Desktop'),
            'Documents': os.path.join(user_profile, 'Documents'),
            'Pictures': os.path.join(user_profile, 'Pictures'),
        }
        jobs = self.settings.get('file_backup_jobs', [])
        existing_sources = {j['source'] for j in jobs}
        added = []
        for name, path in candidates.items():
            path = os.path.normpath(path)
            if os.path.isdir(path) and path not in existing_sources:
                destination = os.path.join(self.backup_dir, name)
                jobs.append({'source': path, 'destination': destination, 'last_run': ''})
                added.append(name)

        if not added:
            QMessageBox.information(self, 'Nothing To Add',
                'Desktop, Documents, and Pictures are already in the list (or not found on this system).')
            return

        self.settings['file_backup_jobs'] = jobs
        save_settings(self.settings)
        self.loadFileBackupJobs()
        self.logActivity(f"File backup jobs quick-added: {', '.join(added)}")

    # ---- System Recovery Extras ----

    def appendBackupLog(self, text):
        self.backup_log.append(text)
        self.backup_log.verticalScrollBar().setValue(self.backup_log.verticalScrollBar().maximum())

    def setBackupButtonsEnabled(self, enabled):
        for btn in self.backup_action_buttons:
            btn.setEnabled(enabled)

    def runBackupCommand(self, steps, completion_title, completion_message):
        """Run CommandThread steps triggered from the Backups page - streams
        full output into self.backup_log (not just a transient status-bar
        line) so it's obvious the operation is actually doing something."""
        if self.command_thread is not None and self.command_thread.isRunning():
            QMessageBox.information(self, 'Busy', 'Another operation is already running - see the log below for progress.')
            return
        self.setBackupButtonsEnabled(False)
        self.status_bar.showMessage(f'{completion_title} - running...')
        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(0, 0)
        self.appendBackupLog(f'\n=== {completion_title} - starting ===')
        self.command_thread = CommandThread(steps)
        self.command_thread.output.connect(self.appendBackupLog)
        self.command_thread.finished.connect(lambda: self._onBackupCommandFinished(completion_title, completion_message))
        self.command_thread.start()

    def _onBackupCommandFinished(self, title, message):
        self.setBackupButtonsEnabled(True)
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setVisible(False)
        self.status_bar.showMessage(f'{title} - done', 5000)
        self.appendBackupLog(f'=== {title} - done ===\n')
        self.logActivity(f'Backup: {title}')
        QMessageBox.information(self, title, message)

    def showProductKey(self):
        """Retrieve and optionally save the Windows product key"""
        self.status_bar.showMessage('Retrieving product key...')
        try:
            result = subprocess.run(
                'powershell -NoProfile -Command "(Get-WmiObject -query \'select * from SoftwareLicensingService\').OA3xOriginalProductKey"',
                shell=True, capture_output=True, text=True, timeout=10,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0
            )
            key = strip_ansi(result.stdout.strip())
        except Exception as e:
            QMessageBox.warning(self, 'Error', f'Could not retrieve product key:\n{e}')
            return

        if not key:
            QMessageBox.information(self, 'Product Key',
                'No embedded product key was found on this system (common on OEM/digital license activations).')
            return

        self.logActivity(f'Product key retrieved: {key}')
        QMessageBox.information(self, 'Windows Product Key', f'Product Key:\n\n{key}\n\nSave this somewhere secure.')

        save_it = QMessageBox.question(self, 'Save to File?', 'Save this key to a text file?', QMessageBox.Yes | QMessageBox.No)
        if save_it == QMessageBox.Yes:
            path, _ = QFileDialog.getSaveFileName(
                self, 'Save Product Key', os.path.join(self.backup_dir, 'product_key.txt'), 'Text files (*.txt)')
            if path:
                try:
                    with open(path, 'w', encoding='utf-8') as f:
                        f.write(f'Windows Product Key: {key}\nRetrieved: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}\n')
                    QMessageBox.information(self, 'Saved', f'Saved to:\n{path}')
                except Exception as e:
                    QMessageBox.warning(self, 'Error', f'Could not save file:\n{e}')

    def backupDrivers(self):
        """Export all installed third-party drivers via DISM"""
        if not self.is_admin:
            QMessageBox.warning(self, 'Admin Required', 'Backing up drivers requires Administrator rights.')
            return
        folder = QFileDialog.getExistingDirectory(self, 'Select Backup Destination', self.backup_dir)
        if not folder:
            return
        driver_path = os.path.join(folder, 'Drivers_' + datetime.now().strftime('%Y%m%d'))
        steps = [(f'Exporting drivers to {driver_path}...',
                   f'dism /online /export-driver /destination:"{driver_path}"', 300)]
        self.runBackupCommand(steps, 'Drivers Backed Up', f'Drivers have been backed up to:\n{driver_path}')

    def _browserProfilePaths(self):
        return {
            'Chrome': os.path.join(os.environ.get('LOCALAPPDATA', ''), 'Google', 'Chrome', 'User Data'),
            'Edge': os.path.join(os.environ.get('LOCALAPPDATA', ''), 'Microsoft', 'Edge', 'User Data'),
            'Firefox': os.path.join(os.environ.get('APPDATA', ''), 'Mozilla', 'Firefox', 'Profiles'),
        }

    def backupBrowserData(self):
        """Mirror Chrome/Edge/Firefox profile folders to a backup destination"""
        dest = QFileDialog.getExistingDirectory(self, 'Select Backup Destination', self.backup_dir)
        if not dest:
            return
        QMessageBox.information(self, 'Close Your Browsers',
            'For best results, close Chrome, Edge, and Firefox before continuing.')

        pairs = [(path, os.path.join(dest, name)) for name, path in self._browserProfilePaths().items() if os.path.isdir(path)]
        if not pairs:
            QMessageBox.warning(self, 'Nothing Found', 'No browser profile folders were found on this system.')
            return
        if self.file_mirror_thread is not None and self.file_mirror_thread.isRunning():
            QMessageBox.information(self, 'Busy', 'A file operation is already running.')
            return

        self.setBackupButtonsEnabled(False)
        self.appendBackupLog(f'\n=== Browser Data Backup - starting ({len(pairs)} browser(s)) ===')
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(0)
        self.file_mirror_thread = FileMirrorThread(pairs)
        self.file_mirror_thread.progress.connect(self.onFileMirrorProgress)
        self.file_mirror_thread.finished.connect(lambda r: self.displayFileBackupResults(r, is_restore=False))
        self.file_mirror_thread.start()
        self.logActivity(f'Browser data backup started -> {dest}')

    def restoreBrowserData(self):
        """Restore Chrome/Edge/Firefox profile folders from a backup"""
        src = QFileDialog.getExistingDirectory(self, 'Select Browser Backup Folder', self.backup_dir)
        if not src:
            return

        pairs = [(os.path.join(src, name), path) for name, path in self._browserProfilePaths().items()
                 if os.path.isdir(os.path.join(src, name))]
        if not pairs:
            QMessageBox.warning(self, 'Nothing Found', f'No browser folders (Chrome/Edge/Firefox) were found inside:\n{src}')
            return

        reply = QMessageBox.warning(
            self, 'Confirm Restore - Close Your Browsers First',
            'CLOSE ALL BROWSERS before continuing, or this restore may fail or corrupt your profile.\n\n'
            f'This will overwrite current browser data with the backup from:\n{src}\n\nContinue?',
            QMessageBox.Yes | QMessageBox.No
        )
        if reply != QMessageBox.Yes:
            return
        if self.file_mirror_thread is not None and self.file_mirror_thread.isRunning():
            QMessageBox.information(self, 'Busy', 'A file operation is already running.')
            return

        self.setBackupButtonsEnabled(False)
        self.appendBackupLog(f'\n=== Browser Data Restore - starting ({len(pairs)} browser(s)) ===')
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(0)
        self.file_mirror_thread = FileMirrorThread(pairs)
        self.file_mirror_thread.progress.connect(self.onFileMirrorProgress)
        self.file_mirror_thread.finished.connect(lambda r: self.displayFileBackupResults(r, is_restore=True))
        self.file_mirror_thread.start()
        self.logActivity(f'Browser data restore started <- {src}')

    def exportFullRegistry(self):
        """Export HKLM\\SYSTEM, HKLM\\SOFTWARE, and HKCU to real .reg files"""
        if not self.is_admin:
            QMessageBox.warning(self, 'Admin Required', 'Exporting the registry requires Administrator rights.')
            return
        dest = QFileDialog.getExistingDirectory(self, 'Select Backup Destination', self.backup_dir)
        if not dest:
            return

        reg_path = os.path.join(dest, 'Registry_' + datetime.now().strftime('%Y%m%d_%H%M%S'))
        try:
            os.makedirs(reg_path, exist_ok=True)
        except Exception as e:
            QMessageBox.warning(self, 'Error', f'Could not create folder:\n{e}')
            return

        hives = [('HKLM\\SYSTEM', 'System.reg'), ('HKLM\\SOFTWARE', 'Software.reg'), ('HKCU', 'CurrentUser.reg')]
        steps = [
            (f'Exporting {hive}...', f'reg export "{hive}" "{os.path.join(reg_path, filename)}" /y', 60)
            for hive, filename in hives
        ]
        self.runBackupCommand(steps, 'Registry Export Complete',
            f'Registry exported to:\n{reg_path}\n\nEach .reg file can be double-clicked to restore that hive.')

    def importRegistryFile(self):
        """Import a .reg file (double-click equivalent, run through reg.exe)"""
        if not self.is_admin:
            QMessageBox.warning(self, 'Admin Required', 'Importing a registry file requires Administrator rights.')
            return
        file_path, _ = QFileDialog.getOpenFileName(self, 'Select .reg File', self.backup_dir, 'Registry files (*.reg)')
        if not file_path:
            return

        reply = QMessageBox.warning(
            self, 'Confirm Registry Import',
            f'Import this registry file?\n\n{file_path}\n\n'
            'This will modify system/user settings and cannot be easily undone. Continue?',
            QMessageBox.Yes | QMessageBox.No
        )
        if reply != QMessageBox.Yes:
            return

        steps = [(f'Importing {os.path.basename(file_path)}...', f'reg import "{file_path}"', 60)]
        self.runBackupCommand(steps, 'Registry Import Complete', f'{os.path.basename(file_path)} has been imported.')

    def updateSetting(self, key, value):
        """Toggle a checkbox-backed setting and persist it immediately"""
        self.settings[key] = bool(value)
        save_settings(self.settings)

    def updateFontSize(self, size):
        """Apply a new base font size app-wide and persist it"""
        self.settings['font_size'] = size
        save_settings(self.settings)
        font = QApplication.instance().font()
        font.setPointSize(size)
        QApplication.instance().setFont(font)

    # ==================== SYSTEM TOOLS ====================
    
    def openDiskCleanup(self):
        """Open Windows Disk Cleanup."""
        try:
            subprocess.Popen('cleanmgr')
            self.logActivity('Opened: Disk Cleanup')
        except Exception:
            QMessageBox.warning(self, 'Error', 'Could not open Disk Cleanup')
    
    def openDefrag(self):
        """Open Windows Defragmenter."""
        try:
            subprocess.Popen('dfrgui')
            self.logActivity('Opened: Defragmenter')
        except Exception:
            QMessageBox.warning(self, 'Error', 'Could not open Defragmenter')
    
    def openTaskManager(self):
        """Open Task Manager."""
        try:
            subprocess.Popen('taskmgr')
            self.logActivity('Opened: Task Manager')
        except Exception:
            QMessageBox.warning(self, 'Error', 'Could not open Task Manager')
    
    def openServices(self):
        """Open Services."""
        try:
            # services.msc isn't an executable - unlike cleanmgr/dfrgui/taskmgr
            # above, it needs to be launched through mmc.exe as an argument.
            subprocess.Popen(['mmc', 'services.msc'])
            self.logActivity('Opened: Services')
        except Exception:
            QMessageBox.warning(self, 'Error', 'Could not open Services')
    
    def openRegedit(self):
        """Open Registry Editor with safety warning."""
        reply = QMessageBox.warning(
            self, 'Warning',
            'Registry Editor is a powerful tool. Incorrect changes can damage Windows.\n\nContinue?',
            QMessageBox.Yes | QMessageBox.No
        )
        if reply == QMessageBox.Yes:
            try:
                subprocess.Popen('regedit')
                self.logActivity('Opened: Registry Editor')
            except Exception:
                QMessageBox.warning(self, 'Error', 'Could not open Registry Editor')
    
    def openSystemInfo(self):
        """Open System Information."""
        try:
            subprocess.Popen('msinfo32')
            self.logActivity('Opened: System Information')
        except Exception:
            QMessageBox.warning(self, 'Error', 'Could not open System Information')
    
    def optimizeRAM(self):
        """Optimize RAM usage using psutil and garbage collection."""
        try:
            import psutil
            import gc
            before = psutil.virtual_memory().percent
            gc.collect()
            try:
                # Force Windows to clear the working set of all processes
                ctypes.windll.psapi.EmptyWorkingSet(-1)
            except Exception: 
                pass
            after = psutil.virtual_memory().percent
            freed = before - after
            QMessageBox.information(self, 'RAM Optimization', f'Memory optimized!\n\nBefore: {before}%\nAfter: {after}%\nFreed: {freed:.1f}%')
            self.logActivity(f'RAM optimized: {freed:.1f}% freed')
        except ImportError:
            QMessageBox.information(self, 'Required', 'Please install psutil: pip install psutil')
    
    def analyzeDisk(self):
        """Analyze disk space across partitions."""
        try:
            import psutil
            info = []
            for partition in psutil.disk_partitions():
                try:
                    usage = psutil.disk_usage(partition.mountpoint)
                    info.append(f"<b>{partition.device}</b><br>Total: {usage.total / (1024**3):.2f} GB<br>Used: {usage.used / (1024**3):.2f} GB ({usage.percent}%)<br><br>")
                except: 
                    pass
            QMessageBox.information(self, 'Disk Analysis', ''.join(info))
            self.logActivity('Disk analyzed')
        except ImportError:
            QMessageBox.information(self, 'Required', 'Please install psutil: pip install psutil')

    def applySelectedTweaks(self):
        """Apply every checked Windows tweak"""
        selected = []
        for row in range(self.tweaks_table.rowCount()):
            checkbox_widget = self.tweaks_table.cellWidget(row, 0)
            if checkbox_widget:
                checkbox = checkbox_widget.findChild(QCheckBox)
                if checkbox and checkbox.isChecked():
                    selected.append(WINDOWS_TWEAKS[row])

        if not selected:
            QMessageBox.warning(self, 'No Selection', 'Check one or more tweaks to apply!')
            return

        needs_admin = [t for t in selected if t.get('admin') and not self.is_admin]
        applicable = [t for t in selected if not (t.get('admin') and not self.is_admin)]
        if needs_admin:
            names = '\n'.join(f"- {t['name']}" for t in needs_admin)
            QMessageBox.warning(self, 'Admin Required',
                f'These tweaks need Administrator rights and will be skipped:\n\n{names}')
        if not applicable:
            return

        destructive = [t for t in applicable if t.get('destructive')]
        preview = '\n'.join(f"- {t['name']}" for t in applicable)
        warning_note = '\n\nWARNING: one or more selected tweaks are destructive or hard to reverse (see names above).' if destructive else ''

        reply = QMessageBox.question(
            self, 'Apply Tweaks', f'Apply {len(applicable)} tweak(s)?\n\n{preview}{warning_note}',
            QMessageBox.Yes | QMessageBox.No
        )
        if reply != QMessageBox.Yes:
            return
        if self.command_thread is not None and self.command_thread.isRunning():
            QMessageBox.information(self, 'Busy', 'Another operation is already running.')
            return

        steps = []
        restart_explorer_needed = False
        for tweak in applicable:
            combined = ' & '.join(tweak['commands'])
            steps.append((f"Applying: {tweak['name']}", combined, 15))
            if tweak.get('restart_explorer'):
                restart_explorer_needed = True
        if restart_explorer_needed:
            steps.append(('Restarting Explorer to apply changes...', 'taskkill /f /im explorer.exe & start explorer.exe', 15))

        finish_note = ' Explorer was restarted to show the changes.' if restart_explorer_needed else ''
        self.runBackupCommand(steps, 'Tweaks Applied', f'{len(applicable)} tweak(s) applied.{finish_note}')

    # ==================== UTILITY FUNCTIONS ====================
    
    def updateProgress(self, message, percent):
        """Update global progress bar."""
        self.progress_bar.setValue(percent)
        self.status_bar.showMessage(message)
    
    def logActivity(self, message):
        """Log events to the dashboard."""
        if self.activity_log is not None:
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            self.activity_log.append(f'[{timestamp}] {message}')
            if self.activity_log.document().lineCount() > 100:
                cursor = self.activity_log.textCursor()
                cursor.movePosition(cursor.Start)
                cursor.movePosition(cursor.Down, cursor.KeepAnchor, 10)
                cursor.removeSelectedText()
    
    def quickScan(self):
        """Quick scan - registry only"""
        self.showPage('registry')
        self.scanRegistry()
    
    def showHelp(self):
        """Show help dialog"""
        help_text = """
        <h2>REGwintool - Help</h2>
        
        <h3>Getting Started</h3>
        <p>Use the sidebar to navigate between different tools.</p>
        
        <h3>Features</h3>
        <ul>
            <li><b>Registry Cleaner:</b> Finds and fixes invalid registry entries</li>
            <li><b>Junk Files:</b> Removes temporary and unnecessary files</li>
            <li><b>Startup Manager:</b> Control which programs start with Windows</li>
            <li><b>Privacy Tools:</b> Clear browsing data and personal information</li>
            <li><b>Programs:</b> View, uninstall (bulk), and install software via winget</li>
            <li><b>Repair:</b> Windows Update/Defender repair, SFC/DISM, health check, restore points</li>
            <li><b>Tools:</b> Windows utilities plus one-click Windows tweaks</li>
            <li><b>Backups:</b> Settings snapshots, real file backups, drivers, browser data, full registry export</li>
        </ul>
        
        <h3>Administrator Mode</h3>
        <p>Some features require Administrator privileges. Use "Restart as Administrator" to enable all features.</p>
        
        <h3>Safety</h3>
        <p>WARNING: Always create backups before making system changes!</p>
        """
        
        msg = QMessageBox(self)
        msg.setWindowTitle('Help')
        msg.setTextFormat(Qt.RichText)
        msg.setText(help_text)
        msg.setStandardButtons(QMessageBox.Ok)
        msg.exec_()
    
    def showAbout(self):
        """Show about dialog"""
        mode = "ADMIN" if self.is_admin else "USER"
        about_text = f"""
        <h2>REGwintool v2.1</h2>
        <p><b>Mode:</b> {mode}</p>
        <br>
        <p><b>Complete System Optimization Suite for Windows</b></p>
        <br>
        <p><b>Features:</b></p>
        <ul>
            <li>Registry Cleaner</li>
            <li>Junk File Remover</li>
            <li>Startup Manager</li>
            <li>Privacy Tools</li>
            <li>Program Uninstaller & Software Installer (winget)</li>
            <li>Repair Tools (Windows Update, Defender, SFC/DISM)</li>
            <li>Backup System (settings, files, drivers, browser data, registry)</li>
            <li>System Tools & Windows Tweaks</li>
        </ul>
        <br>
        <p><b>Developed by:</b> Ronald Goodchild</p>
        <p><b>Company:</b> REGTeches</p>
        <p><b>Website:</b> <a href="http://www.regteches.com/software.html" style="color:#f9e2af;">www.regteches.com/software.html</a></p>
        <br>
        <p>Built with Python and PyQt5</p>
        <p>© 2024 REGTeches. All rights reserved.</p>
        """
        
        QMessageBox.about(self, 'About REGwintool', about_text)

def main():
    import traceback
    # Error logging for technician diagnostics
    log_path = os.path.join(os.environ.get('TEMP', 'C:\\Temp'), 'regwintool_errors.log')
    def qt_exception_hook(exc_type, exc_value, exc_tb):
        msg = ''.join(traceback.format_exception(exc_type, exc_value, exc_tb))
        with open(log_path, 'a', encoding='utf-8') as f:
            f.write(f"\n[{datetime.now()}]\n{msg}\n")
        sys.__excepthook__(exc_type, exc_value, exc_tb)
    sys.excepthook = qt_exception_hook
    app = QApplication(sys.argv)
    app.setStyle('Fusion')
    app.setApplicationName('REGwintool')

    settings = load_settings()
    font = app.font()
    font.setPointSize(settings.get('font_size', 10))
    app.setFont(font)

    window = WinToolsApp()
    window.show()
    sys.exit(app.exec_())

if __name__ == '__main__':
    # Only force a UAC prompt on startup if the user opted into that under
    # Settings -> Startup Behavior. Otherwise launch in User Mode and let
    # them hit "Restart as Administrator" only when they actually need to.
    if load_settings().get('launch_as_admin') and not is_admin():
        if run_as_admin():
            sys.exit(0)
    main()

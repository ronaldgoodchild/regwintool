# REGWinTool

A free **Windows cleanup, repair and tuning suite** with a modern PyQt5 GUI: registry cleaner, junk-file remover, startup manager, privacy tools, bulk uninstaller, Windows Update / Defender repair, one-click tweaks and full backups - all in one window.

> Successor to "WinTools Professional". Built by a working IT technician for tidy-ups and repairs on client PCs. Free to use, free to change.

## Features

| Page | What it does |
|------|--------------|
| **Registry Cleaner** | Finds and fixes invalid registry entries |
| **Junk Files** | Removes temporary and unnecessary files |
| **Startup Manager** | Control which programs start with Windows |
| **Privacy Tools** | Clear browsing data and personal traces |
| **Programs** | View, **bulk uninstall**, and install software via winget |
| **Repair** | Windows Update and Defender repair, SFC / DISM, health check, restore points |
| **Tools** | Windows utilities and one-click tweaks (destructive ones are flagged and need confirmation) |
| **Backups** | Settings snapshots, real file backups, drivers, browser data, full registry export |

Also: a dashboard with an activity log and progress bar, and a "Restart as Administrator" button.

## Requirements

- Windows 10 / 11
- Python 3.9+, `PyQt5`, `psutil` (`pip install -r requirements.txt`)
- Run as **Administrator** for repairs and registry work

## Quick start

```powershell
git clone https://github.com/ronaldgoodchild/regwintool.git
cd regwintool
pip install -r requirements.txt
python regwintool.py
```

## Safety - please read

Registry cleaners and "repair" tools can break a system if used carelessly. **Create a restore point and a backup first** (the Backups page does both). Use it only on machines you own or are authorised to service. No warranty - see [LICENSE](LICENSE).

## Contributing

Ideas and pull requests welcome - see [CONTRIBUTING.md](CONTRIBUTING.md) and [ROADMAP.md](ROADMAP.md).

## License

[MIT](LICENSE) (c) 2026 Ronald Goodchild / REGTeches

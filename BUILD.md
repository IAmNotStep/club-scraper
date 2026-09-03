# Building a standalone app

The scraper can be packaged into a single double-clickable file so teammates can run it
without installing Python. Build on the OS you're targeting — PyInstaller does not
cross-compile, so a Windows `.exe` must be built on Windows and a Mac binary on a Mac.

## Windows

```powershell
pip install -r requirements.txt
pip install pyinstaller
pyinstaller --noconfirm DesignNationScraper.spec
```

Output: `dist\DesignNationScraper.exe`

Or without the spec file:

```powershell
pyinstaller --noconfirm --onefile --console ^
  --name "DesignNationScraper" ^
  --add-data "platform_cache.json;." ^
  app.py
```

Note the semicolon `;` in `--add-data` — Windows uses `;`, Mac and Linux use `:`.

## macOS

```bash
pip3 install -r requirements.txt
pip3 install pyinstaller

python3 -m PyInstaller --noconfirm --onefile --console \
  --name "DesignNationScraper" \
  --add-data "platform_cache.json:." \
  app.py
```

Output: `dist/DesignNationScraper`

Run it with `./dist/DesignNationScraper`. It starts the server on
<http://localhost:8080> and opens your browser; closing the Terminal window stops it.

## Sharing the build

Built binaries are published as [GitHub Release](../../releases) assets rather than
committed to the repo — they're 15–20 MB each and Git handles large binaries poorly. When
you cut a new build, tag a version and upload the Windows and Mac executables as assets on
that release; the README always links to `/releases/latest`, so it picks up the newest one
automatically.

## Troubleshooting

| Problem | Fix |
|---|---|
| `Permission denied` on Mac | `chmod +x dist/DesignNationScraper` |
| macOS blocks "unidentified developer" | System Settings → Privacy & Security → **Open Anyway** |
| Windows SmartScreen warning | More info → Run anyway (unsigned builds always trigger this) |
| Port 8080 already in use | Change `port = 8080` near the bottom of `app.py` |
| Cache not saving | The packaged app writes `platform_cache.json` next to the executable — make sure that folder is writable |

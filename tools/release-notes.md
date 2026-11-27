## Download

| Your computer | File |
|---|---|
| Mac with Apple silicon, macOS 15 or newer | `PySME-@VERSION@-macOS-arm64.dmg` |
| Windows, 64-bit x86 | `PySME-@VERSION@-Windows-x64.zip` |
| Linux, x86_64 | `PySME-@VERSION@-Linux-x86_64.tar.gz` |

You do not need Python or conda. Installing needs a network connection that
reaches github.com and pypi.org, because Python and PySME are downloaded.
You do not need the *Source code* files that GitHub adds.

### macOS

1. Open the disk image and drag **PySME** onto **Applications**.
2. Open PySME. The app is not signed with an Apple Developer ID, so macOS blocks
   it the first time. In the message that appears choose **Done** (not *Move to
   Bin*), then open *System Settings → Privacy & Security*, scroll down and
   choose *Open Anyway*.

### Windows

1. Extract the whole zip file (right-click, *Extract All…*).
2. In the folder `windows`, double-click **Install PySME.cmd**. If Windows shows
   *Windows protected your PC*, choose *More info → Run anyway*; the scripts are
   not signed.
3. Start PySME from the desktop or the Start menu. It opens in your web browser.

### Linux

1. Unpack the archive.
2. In the unpacked folder, run `linux/install.sh`.
3. Start PySME from the applications menu. It opens in your web browser.

`SHA256SUMS.txt` lists the checksums of the three files.

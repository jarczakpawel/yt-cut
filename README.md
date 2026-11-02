# YT Cut

Prosty program do wycinania fragmentów filmów YouTube na Linuxie i Windows.

## Funkcje

- Pobieranie filmu przez yt-dlp i podgląd w odtwarzaczu
- START/KONIEC ustawiane myszą albo ręcznie z dokładnością zapisu do 1 ms
- Zaznaczony punkt zostaje widoczny na osi czasu (żółty znacznik)
- Po kliknięciu Pauza podgląd wraca do wybranego punktu
- Przyciski: odtwarzanie, pauza, klatka wstecz/dalej, od START
- Podgląd zaznaczenia, opcjonalna pętla
- Domyślny eksport wideo MP4; również MP3 i WAV
- Opcjonalne podgłaśnianie na początku i ściszanie na końcu
- Pliki w katalogu Pobrane/Downloads: cut1.mp4, cut2.mp3 itd.
- Nowy URL zwalnia poprzedni film i usuwa pliki tymczasowe

## Windows 10/11 (64-bit)

W zakładce [Actions](https://github.com/jarczakpawel/yt-cut/actions/workflows/windows.yml) wybierz Windows portable, najnowszy udany przebieg, a następnie artifact YT-Cut-Windows-x64.

1. Pobierz ZIP i rozpakuj cały katalog.
2. Uruchom YT-Cut.exe. Nie przenoś samego EXE poza folder.
3. Nie trzeba osobno instalować Pythona, FFmpeg, yt-dlp ani Node.js.

Windows build jest wykonywany przez GitHub Actions na windows-latest z PySide6, PyInstaller i dołączonymi narzędziami. Niepodpisany cyfrowo EXE może wyświetlić ostrzeżenie Microsoft SmartScreen. Dostęp do YouTube wymaga internetu; serwis może zmieniać mechanizmy dostępu.

## Debian (KDE/X11/XWayland)

Zależności: Python 3 z GTK 3 i GObject Introspection (python3-gi, gir1.2-gtk-3.0), mpv, ffmpeg, ffprobe, yt-dlp i Node.js >=22.

Uruchom: python3 yt-cut.py

Na istniejącej instalacji Debiana źródło znajduje się w /home/debian/Programy/YT Cut/yt-cut.py.

Program Linux wykorzystuje mpv jako odtwarzacz (do podglądu i krokowania klatek). Wersja Windows wykorzystuje Qt Multimedia. Eksport dokładnego fragmentu wykonuje FFmpeg z ponownym kodowaniem.

## Ustawianie granic

Kliknięcie na osi czasu wybiera punkt i ustawia tam odtwarzanie. Ten punkt jest oznaczony kolorem żółtym. Podczas podglądu biały kursor odtwarzania się przesuwa. Pauza przywraca żółty punkt. Ustaw START z punktu i Ustaw KONIEC z punktu przypisują granicę do zaznaczonej pozycji.

Shift+klik na osi czasu ustawia START, Ctrl+klik ustawia KONIEC. Można też przeciągać czerwone znaczniki lub edytować dokładne czasy.

Czasy są zapisywane i eksportowane w milisekundach. W zależności od klatek źródłowego filmu podgląd w Qt Multimedia może lądować na najbliższej zdekodowanej klatce; nie oznacza to dokładności do pojedynczej klatki dla każdego kodeka.

## Narzędzia i licencje

Pakiet Windows zawiera binaria FFmpeg (build gyan.dev), yt-dlp i Node.js. Każdy projekt ma własną licencję i kod źródłowy:

- https://ffmpeg.org/legal.html
- https://www.gyan.dev/ffmpeg/builds/
- https://github.com/yt-dlp/yt-dlp
- https://github.com/nodejs/node

Nie umieszczaj w repo pobieranych filmów ani binariów narzędzi. Gotowy ZIP jest dodawany jako artifact workflow.

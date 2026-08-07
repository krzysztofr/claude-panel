' Uruchamia Claude Panel w tle - bez migajacych okien konsoli.
' Ten plik jest celem skrotu w Autostarcie Windows.
Option Explicit

Dim sh, fso, base
Set sh  = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
base = fso.GetParentFolderName(WScript.ScriptFullName)
sh.CurrentDirectory = base

' 0 = okno ukryte, False = nie czekaj na zakonczenie
sh.Run """" & base & "\run-server.bat""", 0, False

' Ekran pobiera dane z serwera, wiec dajemy serwerowi chwile na wstanie.
WScript.Sleep 4000

sh.Run """" & base & "\run-screen.bat""", 0, False

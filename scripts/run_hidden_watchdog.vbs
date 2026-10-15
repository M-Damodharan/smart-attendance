Set WshShell = CreateObject("WScript.Shell")
WshShell.CurrentDirectory = "d:\smart-attendance"
WshShell.Run "python d:\smart-attendance\scripts\watchdog_24_7.py", 0, False

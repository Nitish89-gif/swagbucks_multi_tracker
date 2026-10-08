Set WshShell = CreateObject("WScript.Shell")
WshShell.CurrentDirectory = "C:\Users\pasum\swagbucks_multi_tracker"
WshShell.Run "cmd /c "".venv\Scripts\pythonw.exe service_runner.py""", 0, False

Set WshShell = CreateObject("WScript.Shell")
WshShell.CurrentDirectory = "C:\Users\pasum\swagbucks_multi_tracker"
WshShell.Run """C:\Users\pasum\swagbucks_multi_tracker\.venv\Scripts\pythonw.exe"" ""C:\Users\pasum\swagbucks_multi_tracker\service_runner.py""", 0, False

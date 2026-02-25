@echo off
:: Launch VS Code through the envoy vscode wrapper.
::
:: --inherit-env is required so that the wrapper's Python process can locate
:: the 'python' interpreter on PATH.  The wrapper then spawns VS Code directly
:: (not through envoy), so the full system environment is only held for the
:: brief wrapper process, not inherited by VS Code itself.
envoy --inherit-env vscode %*

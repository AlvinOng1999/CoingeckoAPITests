@echo off

if not exist venv (
    echo Creating virtual environment...
    python -m venv venv
)

echo Activating virtual environment...
call venv\Scripts\activate.bat

echo Installing dependencies...
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

echo Installing Playwright browsers...
python -m playwright install

echo.
echo Done! Virtual environment is active.
echo Run "venv\Scripts\activate.bat" next time to activate it.

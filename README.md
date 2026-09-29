## How to Setup BGent
1. Open the command line
2. Use `cd <directory>` to open the directory you want this to live in. You should choose a directory that is saved locally on your computer (don't use onedrive) 
3. Use `git clone https://github.com/lrbailey58-coder/andhacks-2026.git` to clone the github repository and create the project folder
4. Use `cd andhacks-2026` to open the repository
5. Use `pip install uv` to install the package manager
6. Make sure you have python installed. Install it onto your computer if you have not already.
7. Get an API key from google: [Link to Google AI Studio](https://aistudio.google.com). Creating a key is free, but, since this project needs pretty bulky models, you may want to spend some money to make sure the results are good. It should cost less than $0.50 per run for most games. If you have a bulkier game to test, I cannot guarantee that it will work or that it will not cost more than $0.50. 
8. Save your key as an enviornmental variable on your computer. Name the variable `GEMINI_API_KEY`. 
   - FOR WINDOWS: Open the search and search for `environmental variables`. Click on `Edit Environmental Variables for your Account`. Create a new variable, name it `GEMINI_API_KEY` and paste your key as the value. 
   - FOR MAC/LINUX: Use `export GEMINI_API_KEY=[key]`. You will need to do this each time you open BGent
9. Run the program: `uv run python src/web/app.py`
10. Copy and paste the http link into your browser to access BGent!

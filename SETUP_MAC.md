# Mac Setup Guide

## Step 1: Install Python

1. Open **Terminal** (search "Terminal" in Spotlight with Cmd+Space)
2. Type this and press Enter:

```
python3 --version
```

3. If it shows a version (3.10+), skip to Step 2. If not, go to https://www.python.org/downloads/ and install it.

## Step 2: Download the bot

1. Go to https://github.com/s-k-28/nq-es-trader-5k-payout
2. Click the green **Code** button
3. Click **Download ZIP**
4. Extract the ZIP (double-click it in Finder)

## Step 3: Install dependencies

1. Open **Terminal**
2. Type `cd ` (with a space after), then drag the extracted `nq-es-trader-5k-payout` folder into the Terminal window and press Enter
3. Run:

```
pip3 install -r requirements.txt
```

Wait for it to finish.

## Step 4: Set up credentials

1. In the `nq-es-trader-5k-payout` folder, run this in Terminal:

```
cp .env.example .env
```

2. Open the `.env` file:

```
open -e .env
```

3. Fill in your TopStepX credentials:

```
TOPSTEP_USER=your_topstep_username
TOPSTEP_API_KEY=your_api_key
TOPSTEP_ENV=live
CONTRACTS=20
```

4. Save and close

## Step 5: Make the launcher clickable

Run this once in Terminal (from the nq-es-trader-5k-payout folder):

```
chmod +x start_bot.command
```

## Step 6: Run the bot

Double-click `start_bot.command` in Finder.

If macOS says the file can't be opened, go to **System Settings > Privacy & Security**, scroll down, and click **Open Anyway**.

## Stopping the bot

Press `Ctrl+C` in the terminal window. The bot will flatten all open positions and shut down cleanly.

## Troubleshooting

**"command not found: python"** - Use `python3` instead, or install Python from python.org.

**"ModuleNotFoundError"** - Run `pip3 install -r requirements.txt` again.

**"Missing credentials"** - Make sure your `.env` file exists and has the correct username and API key.

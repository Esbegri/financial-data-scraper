# Financial Data Extraction Pipeline

A robust, error-tolerant web scraping pipeline built with Python and Playwright. Designed to extract and consolidate financial statements (Income Statement, Balance Sheet, Cash Flow) from dynamic, JS-rendered financial platforms at scale.

## Key Features
- **Dynamic Content Handling:** Automatically manages overlays, login states, and dynamic DOM elements using Playwright.
- **Resilient Batch Processing:** Built-in tracking system allows the script to resume from the last successful URL after interruptions.
- **Data Normalization:** Automatically cleans irrelevant headers and consolidates multi-tab financial data into clean, row-oriented CSV formats.
- **Industry Intelligence:** Extracts company metadata including Ticker, Country, Sector, and Industry for deeper financial analysis.

## Usage
1. Prepare your `urls_50.txt` file in the root directory.
2. Ensure you have the required dependencies installed.
3. Run the script: `python step5.py`

## Professional Disclaimer
This repository is for educational and research purposes only. Please ensure your scraping activities comply with the target website's Terms of Service.
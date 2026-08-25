"""
Simple test to verify Playwright opens a visible browser
"""
import asyncio
from playwright.async_api import async_playwright

async def test_browser():
    print("🎭 Starting Playwright test with Microsoft Edge...")
    
    async with async_playwright() as p:
        print("✅ Playwright initialized")
        
        # Launch Microsoft Edge browser in headed mode (visible)
        browser = await p.chromium.launch(channel='msedge', headless=False, slow_mo=1000)
        print("✅ Microsoft Edge launched (you should see Edge window!)")
        
        # Create a page
        page = await browser.new_page()
        print("✅ New page created")
        
        # Navigate to a website
        await page.goto("https://www.google.com")
        print("✅ Navigated to Google")
        
        # Wait so you can see it
        print("⏸️ Waiting 5 seconds so you can see Edge browser...")
        await asyncio.sleep(5)
        
        # Close
        await browser.close()
        print("✅ Test complete! Edge browser closed.")

if __name__ == "__main__":
    asyncio.run(test_browser())

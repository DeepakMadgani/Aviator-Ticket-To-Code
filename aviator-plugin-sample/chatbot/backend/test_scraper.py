"""
Test Playwright ValueEdge Scraper
Quick test to verify scraper works with your credentials
"""

import asyncio
import sys
from pathlib import Path

# Add services to path
sys.path.insert(0, str(Path(__file__).parent))

from services.valueedge_scraper import ValueEdgeScraper

async def test_scraper():
    """Test the scraper with manual configuration"""
    
    print("=" * 80)
    print("🎭 PLAYWRIGHT VALUEEDGE SCRAPER TEST")
    print("=" * 80)
    
    # Get configuration from user
    print("\n📝 Enter ValueEdge Configuration:")
    valueedge_url = input("ValueEdge URL: ").strip()
    username = input("Username: ").strip()
    
    import getpass
    password = getpass.getpass("Password: ").strip()
    
    ticket_id = input("Ticket ID (e.g., VE-CB-1234): ").strip()
    
    print(f"\n🚀 Starting scraper...")
    print(f"URL: {valueedge_url}")
    print(f"User: {username}")
    print(f"Ticket: {ticket_id}")
    print(f"Mode: Visible browser (headless=False)")
    
    try:
        async with ValueEdgeScraper(
            valueedge_url=valueedge_url,
            username=username,
            password=password,
            headless=False,  # Show browser for debugging
            screenshot_on_error=True
        ) as scraper:
            
            print(f"\n📥 Fetching ticket {ticket_id}...")
            ticket = await scraper.get_ticket(ticket_id)
            
            if ticket:
                print("\n" + "=" * 80)
                print("✅ SUCCESS! Ticket Data:")
                print("=" * 80)
                print(f"\n🎫 Ticket ID: {ticket.ticket_id}")
                print(f"📋 Title: {ticket.title}")
                print(f"\n📝 Description:")
                print("-" * 80)
                print(ticket.description[:500])
                if len(ticket.description) > 500:
                    print("...")
                print("-" * 80)
                print(f"\n⚡ Priority: {ticket.priority}")
                print(f"📊 Status: {ticket.status}")
                
                if ticket.assignee:
                    print(f"👤 Assignee: {ticket.assignee}")
                
                if ticket.labels:
                    print(f"🏷️  Labels: {', '.join(ticket.labels)}")
                
                if ticket.acceptance_criteria:
                    print(f"\n✅ Acceptance Criteria ({len(ticket.acceptance_criteria)}):")
                    for i, criterion in enumerate(ticket.acceptance_criteria, 1):
                        print(f"   {i}. {criterion}")
                
                if ticket.created_date:
                    print(f"\n📅 Created: {ticket.created_date}")
                
                if ticket.updated_date:
                    print(f"📅 Updated: {ticket.updated_date}")
                
                if ticket.estimated_hours:
                    print(f"⏱️  Estimated: {ticket.estimated_hours}h")
                
                if ticket.actual_hours:
                    print(f"⏱️  Actual: {ticket.actual_hours}h")
                
                print("\n" + "=" * 80)
                print("📸 Screenshot saved: ticket_{}.png".format(ticket_id))
                print("=" * 80)
                
                # Save to JSON for inspection
                import json
                output_file = f"ticket_{ticket_id}.json"
                with open(output_file, "w", encoding="utf-8") as f:
                    json.dump(ticket.dict(), f, indent=2, default=str)
                print(f"💾 Full data saved to: {output_file}")
                
            else:
                print("\n❌ FAILED to fetch ticket")
                print("Check error screenshot: error_{}.png".format(ticket_id))
                
    except KeyboardInterrupt:
        print("\n\n⚠️  Test interrupted by user")
    except Exception as e:
        print(f"\n❌ ERROR: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    print("\n⚠️  Make sure ValueEdge is accessible and credentials are correct!\n")
    asyncio.run(test_scraper())

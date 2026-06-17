"""
End-to-End Test Script for Aviator Platform
Tests complete workflow from ticket to code generation
"""
import httpx
import asyncio
import json
from typing import Dict

BASE_URL = "http://localhost:8000"

class AviatorE2ETest:
    def __init__(self):
        self.client = httpx.AsyncClient(timeout=60.0)
        self.workflow_id = None
        self.repo_path = "C:\\Supplier_exchange\\area-service"
    
    async def test_complete_workflow(self, ticket_description: str):
        """Run complete workflow test."""
        print("=" * 80)
        print("🚀 Aviator Platform - End-to-End Test")
        print("=" * 80)
        print()
        
        try:
            # Step 1: Start workflow
            print("📋 Step 1: Starting workflow...")
            workflow_data = await self.start_workflow(ticket_description)
            self.workflow_id = workflow_data['workflow_id']
            print(f"✅ Workflow created: {self.workflow_id}")
            print()
            
            # Wait for classification
            await asyncio.sleep(3)
            
            # Step 2: Check classification
            print("🔍 Step 2: Checking classification...")
            workflow_state = await self.get_workflow_state()
            operation_type = workflow_state.get('operation_type')
            print(f"✅ Classified as: {operation_type}")
            print()
            
            # Step 3: Approve operation
            print("✔️  Step 3: Approving operation...")
            await self.approve_operation()
            print("✅ Operation approved")
            print()
            
            # Wait for localization
            await asyncio.sleep(5)
            
            # Step 4: Check localized files
            print("📁 Step 4: Checking localized files...")
            workflow_state = await self.get_workflow_state()
            candidates = workflow_state.get('candidate_files', [])
            print(f"✅ Found {len(candidates)} candidate files:")
            for i, candidate in enumerate(candidates[:5], 1):
                print(f"   {i}. {candidate['path']} (score: {candidate['score']})")
                if candidate.get('method_name'):
                    print(f"      Method: {candidate['method_name']}()")
            print()
            
            # Step 5: Approve files (select top 2)
            print("✔️  Step 5: Approving selected files...")
            selected_files = [c['path'] for c in candidates[:2] if c.get('selected', True)]
            await self.approve_files(selected_files)
            print(f"✅ Approved {len(selected_files)} files")
            print()
            
            # Wait for impact analysis
            await asyncio.sleep(3)
            
            # Step 6: Check impact analysis
            print("📊 Step 6: Checking impact analysis...")
            workflow_state = await self.get_workflow_state()
            impact = workflow_state.get('impact_analysis', {})
            print(f"✅ Impact Analysis:")
            print(f"   - Direct callers: {impact.get('direct_callers', 0)}")
            print(f"   - Transitive dependents: {impact.get('transitive_dependents', 0)}")
            print(f"   - Affected tests: {impact.get('affected_tests', 0)}")
            print(f"   - Risk level: {impact.get('risk_level', 'unknown')}")
            print()
            
            # Step 7: Continue to generation
            print("🔨 Step 7: Generating code patch...")
            await self.continue_to_generation()
            print("✅ Starting patch generation...")
            print()
            
            # Wait for generation
            await asyncio.sleep(8)
            
            # Step 8: Check generated patch
            print("📝 Step 8: Checking generated patch...")
            workflow_state = await self.get_workflow_state()
            steps = workflow_state.get('steps', [])
            patch_step = None
            for step in steps:
                if step.get('phase') == 'patch_generation':
                    patch_step = step
                    break
            
            if patch_step:
                patch_data = patch_step.get('data', {})
                patch_preview = patch_data.get('patch', '')[:200]
                print(f"✅ Patch generated ({patch_data.get('full_patch_length', 0)} chars)")
                print(f"   Preview: {patch_preview}...")
            else:
                print("⚠️  No patch generated yet")
            print()
            
            # Step 9: Apply patch (optional - commented out for safety)
            print("⏸️  Step 9: Patch application (skipped for safety)")
            print("   To apply: Uncomment the apply_patch() call in this script")
            # await self.apply_patch()
            print()
            
            print("=" * 80)
            print("✅ Test completed successfully!")
            print("=" * 80)
            
            return True
            
        except Exception as e:
            print(f"❌ Test failed: {e}")
            import traceback
            traceback.print_exc()
            return False
        
        finally:
            await self.client.aclose()
    
    async def start_workflow(self, ticket_description: str) -> Dict:
        """Start workflow."""
        response = await self.client.post(
            f"{BASE_URL}/api/workflow/transparent/start",
            json={
                "project_id": "test-project",
                "ticket_id": "TEST-001",
                "ticket_description": ticket_description,
                "repo_path": self.repo_path
            }
        )
        response.raise_for_status()
        return response.json()
    
    async def get_workflow_state(self) -> Dict:
        """Get current workflow state."""
        response = await self.client.get(
            f"{BASE_URL}/api/workflow/transparent/{self.workflow_id}"
        )
        response.raise_for_status()
        return response.json()
    
    async def approve_operation(self):
        """Approve operation type."""
        response = await self.client.post(
            f"{BASE_URL}/api/workflow/transparent/approve-operation",
            json={"workflow_id": self.workflow_id}
        )
        response.raise_for_status()
    
    async def approve_files(self, selected_files: list):
        """Approve selected files."""
        response = await self.client.post(
            f"{BASE_URL}/api/workflow/transparent/approve-files",
            json={
                "workflow_id": self.workflow_id,
                "selected_files": selected_files
            }
        )
        response.raise_for_status()
    
    async def continue_to_generation(self):
        """Continue to patch generation."""
        response = await self.client.post(
            f"{BASE_URL}/api/workflow/transparent/continue-to-generation",
            json={
                "workflow_id": self.workflow_id,
                "repo_path": self.repo_path
            }
        )
        response.raise_for_status()
    
    async def apply_patch(self):
        """Apply generated patch."""
        response = await self.client.post(
            f"{BASE_URL}/api/workflow/transparent/apply-patch",
            json={
                "workflow_id": self.workflow_id,
                "repo_path": self.repo_path
            }
        )
        response.raise_for_status()


async def main():
    """Main test runner."""
    test = AviatorE2ETest()
    
    # Test ticket
    ticket = "Fix transmittal validation error in the area service"
    
    success = await test.test_complete_workflow(ticket)
    
    return 0 if success else 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    exit(exit_code)

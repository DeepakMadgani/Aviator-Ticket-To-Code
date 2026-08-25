"""
RESPONSE VERIFICATION & MONITORING
Check if backend is sending proper responses at each step
"""

import requests
import json
import time
from datetime import datetime

class ResponseMonitor:
    def __init__(self, backend_url='http://localhost:8002'):
        self.backend_url = backend_url
        self.responses = []
        self.print("=" * 80)
        self.print("AVIATOR RESPONSE MONITOR")
        self.print("=" * 80)
    
    def print(self, msg):
        print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")
    
    def check_backend_alive(self):
        """Check if backend is running"""
        try:
            resp = requests.get(f'{self.backend_url}/api/health', timeout=2)
            if resp.status_code == 200 or resp.status_code == 404:
                self.print("✓ Backend is RUNNING")
                return True
        except:
            pass
        
        self.print("✗ Backend is DOWN - Start it first!")
        return False
    
    def list_projects(self):
        """List projects - basic API check"""
        try:
            resp = requests.get(f'{self.backend_url}/api/projects', timeout=5)
            self.print(f"✓ List Projects Response: {resp.status_code}")
            if resp.status_code == 200:
                self.print(f"  Projects: {len(resp.json()) if isinstance(resp.json(), list) else 'N/A'}")
            return resp
        except Exception as e:
            self.print(f"✗ List Projects Failed: {e}")
            return None
    
    def create_project(self, path='.'):
        """Create a project - verify response format"""
        try:
            payload = {'path': path}
            resp = requests.post(f'{self.backend_url}/api/projects', json=payload, timeout=5)
            self.print(f"✓ Create Project Response: {resp.status_code}")
            if resp.status_code in [200, 201]:
                data = resp.json()
                self.print(f"  Project ID: {data.get('id') or data.get('project_id')}")
                return data
            return None
        except Exception as e:
            self.print(f"✗ Create Project Failed: {e}")
            return None
    
    def start_workflow(self, project_id, ticket_desc):
        """Start workflow - key response check"""
        try:
            payload = {
                'project_id': project_id,
                'ticket_description': ticket_desc,
                'repo_path': '.'
            }
            resp = requests.post(f'{self.backend_url}/api/workflow/transparent/start', json=payload, timeout=10)
            self.print(f"✓ Start Workflow Response: {resp.status_code}")
            if resp.status_code in [200, 201]:
                data = resp.json()
                self.print(f"  Workflow ID: {data.get('workflow_id')}")
                self.print(f"  Status: {data.get('status')}")
                return data
            else:
                self.print(f"  Error: {resp.text[:200]}")
            return None
        except Exception as e:
            self.print(f"✗ Start Workflow Failed: {e}")
            return None
    
    def check_workflow_status(self, workflow_id):
        """Check workflow status"""
        try:
            resp = requests.get(f'{self.backend_url}/api/workflow/transparent/{workflow_id}', timeout=5)
            self.print(f"✓ Workflow Status Response: {resp.status_code}")
            if resp.status_code == 200:
                data = resp.json()
                self.print(f"  Current Status: {data.get('status')}")
                self.print(f"  Current Node: {data.get('current_node')}")
                return data
            return None
        except Exception as e:
            self.print(f"✗ Check Status Failed: {e}")
            return None
    
    def monitor_workflow(self, workflow_id, timeout=60):
        """Monitor workflow progress - watch response updates"""
        self.print(f"\nMonitoring workflow {workflow_id}...")
        start = time.time()
        
        while time.time() - start < timeout:
            status = self.check_workflow_status(workflow_id)
            if status:
                self.responses.append(status)
            time.sleep(2)
        
        self.print(f"\nMonitoring complete. {len(self.responses)} status updates received.")
    
    def verify_response_structure(self):
        """Check if responses have expected structure"""
        self.print("\n" + "=" * 80)
        self.print("RESPONSE STRUCTURE VERIFICATION")
        self.print("=" * 80)
        
        for i, resp in enumerate(self.responses):
            self.print(f"\nResponse {i+1}:")
            expected_fields = ['status', 'current_node', 'workflow_id']
            for field in expected_fields:
                if field in resp:
                    self.print(f"  ✓ {field}: {resp[field]}")
                else:
                    self.print(f"  ✗ Missing: {field}")
    
    def summary(self):
        """Print summary of all responses"""
        self.print("\n" + "=" * 80)
        self.print("RESPONSE SUMMARY")
        self.print("=" * 80)
        self.print(f"Total API calls: {len(self.responses)}")
        
        if self.responses:
            statuses = set(r.get('status') for r in self.responses)
            self.print(f"Unique statuses: {statuses}")
            
            nodes = set(r.get('current_node') for r in self.responses if r.get('current_node'))
            self.print(f"Workflow nodes visited: {len(nodes)}")
            for node in nodes:
                self.print(f"  - {node}")

def run_verification():
    """Run complete verification"""
    monitor = ResponseMonitor()
    
    # Check if backend alive
    if not monitor.check_backend_alive():
        return
    
    print()
    
    # List projects
    monitor.list_projects()
    print()
    
    # Create project
    project = monitor.create_project('.')
    if not project:
        monitor.print("Cannot proceed without project")
        return
    
    project_id = project.get('id') or project.get('project_id')
    print()
    
    # Start workflow
    workflow = monitor.start_workflow(project_id, 'Test ticket: fix upload button issue')
    if not workflow:
        monitor.print("Cannot start workflow")
        return
    
    workflow_id = workflow.get('workflow_id')
    print()
    
    # Monitor workflow
    monitor.monitor_workflow(workflow_id, timeout=30)
    print()
    
    # Verify structure
    monitor.verify_response_structure()
    print()
    
    # Summary
    monitor.summary()

if __name__ == '__main__':
    run_verification()

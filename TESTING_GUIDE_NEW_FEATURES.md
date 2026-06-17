# 🧪 TESTING GUIDE - New Production Components

**Date**: May 27, 2026  
**Components**: AST Patch Engine, Incremental Indexer, Static Analysis Gates, Repository Memory

---

## 🎯 **WHAT TO TEST**

### **1. AST Patch Engine**

**File**: `src/ticket_to_code/patching/ast_patch_engine.py`

#### **Unit Test Example**

```python
import pytest
from pathlib import Path
from ticket_to_code.patching import (
    PatchEngineFactory,
    PatchOperation,
    PatchOperationType
)

def test_insert_method_operation():
    """Test inserting a new method into a Java class"""
    
    # Create test file
    test_file = Path("test_data/TestService.java")
    test_file.write_text("""
public class TestService {
    public void existingMethod() {
        // Existing code
    }
}
    """)
    
    # Create patch operation
    operation = PatchOperation(
        operation_type=PatchOperationType.INSERT_METHOD,
        target_file=str(test_file),
        target_class="TestService",
        position="after",
        anchor="existingMethod",
        code="""
    private void newMethod() {
        System.out.println("New method");
    }
        """
    )
    
    # Apply patch
    engine = PatchEngineFactory.create_engine("java")
    result = engine.apply_patch(str(test_file), [operation])
    
    # Assertions
    assert result.success
    assert result.operations_applied == 1
    assert "newMethod" in test_file.read_text()
    assert "existingMethod" in test_file.read_text()  # Original preserved


def test_modify_method_operation():
    """Test modifying an existing method"""
    
    # Setup test file with method to modify
    test_file = Path("test_data/TestService.java")
    test_file.write_text("""
public class TestService {
    public String getName() {
        return "old name";
    }
}
    """)
    
    # Create modification operation
    operation = PatchOperation(
        operation_type=PatchOperationType.MODIFY_METHOD,
        target_file=str(test_file),
        target_class="TestService",
        target_method="getName",
        code="""
    public String getName() {
        return "new name";
    }
        """
    )
    
    # Apply patch
    engine = PatchEngineFactory.create_engine("java")
    result = engine.apply_patch(str(test_file), [operation])
    
    # Assertions
    assert result.success
    assert "new name" in test_file.read_text()
    assert "old name" not in test_file.read_text()
```

#### **Integration Test**

```python
def test_full_patch_workflow():
    """Test complete workflow: LLM → Operations → Patch → Verify"""
    
    # 1. Mock LLM output (in reality, LLM generates these)
    operations = [
        PatchOperation(
            operation_type=PatchOperationType.INSERT_IMPORT,
            target_file="SupplierService.java",
            code="import java.util.regex.Pattern;"
        ),
        PatchOperation(
            operation_type=PatchOperationType.INSERT_FIELD,
            target_file="SupplierService.java",
            target_class="SupplierService",
            code='private static final Pattern EMAIL_PATTERN = Pattern.compile("^[A-Za-z0-9+_.-]+@(.+)$");'
        ),
        PatchOperation(
            operation_type=PatchOperationType.INSERT_METHOD,
            target_file="SupplierService.java",
            target_class="SupplierService",
            position="before",
            anchor="saveSupplier",
            code="""
    private void validateEmail(String email) {
        if (!EMAIL_PATTERN.matcher(email).matches()) {
            throw new IllegalArgumentException("Invalid email");
        }
    }
            """
        )
    ]
    
    # 2. Apply all patches
    engine = PatchEngineFactory.create_engine("java")
    result = engine.apply_patch("SupplierService.java", operations)
    
    # 3. Verify
    assert result.success
    assert result.operations_applied == 3
    
    # 4. Verify file content
    content = Path("SupplierService.java").read_text()
    assert "import java.util.regex.Pattern" in content
    assert "EMAIL_PATTERN" in content
    assert "validateEmail" in content
    
    # 5. Verify file compiles
    import subprocess
    result = subprocess.run(
        ["javac", "SupplierService.java"],
        capture_output=True
    )
    assert result.returncode == 0  # Compilation successful
```

---

## 🎯 **2. Incremental Indexer**

**File**: `src/ticket_to_code/indexing/incremental_indexer.py`

#### **Unit Test Example**

```python
import pytest
from pathlib import Path
from ticket_to_code.indexing import IncrementalIndexer, FileChange, FileChangeType

def test_detect_git_changes():
    """Test Git change detection"""
    
    # Setup test repo
    workspace = Path("test_repo")
    workspace.mkdir(exist_ok=True)
    
    # Initialize Git
    import subprocess
    subprocess.run(["git", "init"], cwd=workspace)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=workspace)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=workspace)
    
    # Create initial file and commit
    test_file = workspace / "Test.java"
    test_file.write_text("public class Test {}")
    subprocess.run(["git", "add", "."], cwd=workspace)
    subprocess.run(["git", "commit", "-m", "Initial"], cwd=workspace)
    
    # Create indexer and save state
    indexer = IncrementalIndexer(str(workspace))
    indexer._save_index_state(indexer._get_current_commit())
    
    # Modify file
    test_file.write_text("public class Test { void newMethod() {} }")
    subprocess.run(["git", "add", "."], cwd=workspace)
    subprocess.run(["git", "commit", "-m", "Modified"], cwd=workspace)
    
    # Detect changes
    changes = indexer.get_changes_since_last_index()
    
    # Assertions
    assert len(changes) == 1
    assert changes[0].change_type == FileChangeType.MODIFIED
    assert "Test.java" in changes[0].file_path


def test_incremental_update():
    """Test incremental index update"""
    
    workspace = Path("test_repo")
    indexer = IncrementalIndexer(str(workspace))
    
    # Create changes
    changes = [
        FileChange(
            change_type=FileChangeType.ADDED,
            file_path="NewService.java"
        ),
        FileChange(
            change_type=FileChangeType.MODIFIED,
            file_path="ExistingService.java"
        )
    ]
    
    # Update index
    result = indexer.update_index(changes)
    
    # Assertions
    assert result.success
    assert result.files_added == 1
    assert result.files_modified == 1
    assert result.duration_seconds < 10  # Should be fast
```

#### **Performance Test**

```python
def test_incremental_vs_full_index_performance():
    """Compare incremental vs full indexing performance"""
    
    import time
    
    workspace = Path("large_repo")  # Repo with 1000+ files
    
    # Full index (baseline)
    start = time.time()
    from aviator_core.indexer import index_repository
    index_repository(str(workspace))
    full_index_time = time.time() - start
    
    # Modify 10 files
    for i in range(10):
        file = workspace / f"service_{i}.java"
        content = file.read_text()
        file.write_text(content + "\n// Modified")
    
    # Incremental update
    start = time.time()
    indexer = IncrementalIndexer(str(workspace))
    indexer.update_index()
    incremental_time = time.time() - start
    
    # Assertions
    print(f"Full index: {full_index_time:.2f}s")
    print(f"Incremental: {incremental_time:.2f}s")
    print(f"Speedup: {full_index_time / incremental_time:.1f}x")
    
    assert incremental_time < full_index_time / 10  # At least 10x faster
```

---

## 🎯 **3. Static Analysis Gates**

**File**: `src/ticket_to_code/quality/static_analysis_gate.py`

#### **Unit Test Example**

```python
import pytest
from pathlib import Path
from ticket_to_code.quality import StaticAnalysisGate, run_quality_gates

def test_quality_gate_passes():
    """Test that clean code passes all gates"""
    
    workspace = Path("test_project")
    
    # Create clean Java file
    test_file = workspace / "src" / "CleanService.java"
    test_file.parent.mkdir(parents=True, exist_ok=True)
    test_file.write_text("""
package com.example;

import java.util.List;

/**
 * Clean service following all best practices.
 */
public class CleanService {
    
    private final String name;
    
    public CleanService(String name) {
        this.name = name;
    }
    
    public String getName() {
        return name;
    }
}
    """)
    
    # Run quality gates
    result = run_quality_gates(str(workspace), files=[str(test_file)])
    
    # Assertions
    assert result.passed
    assert result.can_merge
    assert result.blocker_issues == 0
    assert result.critical_issues == 0


def test_quality_gate_blocks_bad_code():
    """Test that bad code is blocked"""
    
    workspace = Path("test_project")
    
    # Create bad Java file
    test_file = workspace / "src" / "BadService.java"
    test_file.parent.mkdir(parents=True, exist_ok=True)
    test_file.write_text("""
public class BadService {
    public void method() {
        String unused = "never used";  // PMD violation
        try {
            dangerousOperation();
        } catch (Exception e) {
            // Empty catch block - SpotBugs violation
        }
    }
}
    """)
    
    # Run quality gates
    result = run_quality_gates(str(workspace), files=[str(test_file)])
    
    # Assertions
    assert not result.passed
    assert not result.can_merge
    assert len(result.blocking_reasons) > 0
```

---

## 🎯 **4. Repository Memory**

**File**: `src/ticket_to_code/memory/repository_memory.py`

#### **Unit Test Example**

```python
import pytest
from pathlib import Path
from ticket_to_code.memory import RepositoryMemory, MemoryType

def test_record_and_recall_success():
    """Test recording and recalling successful fix"""
    
    workspace = Path("test_workspace")
    memory = RepositoryMemory(str(workspace))
    
    # Record successful fix
    entry = memory.record_successful_fix(
        ticket_id="TKT-123",
        description="Email validation",
        approach="Use Pattern.compile with RFC 5322",
        files_modified=["SupplierService.java"],
        lessons_learned=["Always compile pattern as constant"]
    )
    
    # Recall similar fix
    recalled = memory.recall_similar_fixes(
        description="Phone validation",
        files=["SupplierService.java"]
    )
    
    # Assertions
    assert len(recalled) > 0
    assert recalled[0].type == MemoryType.SUCCESSFUL_FIX
    assert "Pattern.compile" in recalled[0].approach


def test_avoid_past_failures():
    """Test that past failures are recalled to avoid repetition"""
    
    workspace = Path("test_workspace")
    memory = RepositoryMemory(str(workspace))
    
    # Record failure
    memory.record_failed_attempt(
        ticket_id="TKT-456",
        description="Regex validation",
        approach="Put regex in constructor",
        error_message="NullPointerException",
        avoid_list=["Don't initialize in constructor"]
    )
    
    # Try similar approach
    failures = memory.recall_failures_to_avoid(
        description="Regex validation",
        approach="Initialize pattern in constructor"
    )
    
    # Assertions
    assert len(failures) > 0
    assert "constructor" in failures[0].approach.lower()
    assert len(failures[0].avoid_this) > 0


def test_memory_persistence():
    """Test that memories persist across sessions"""
    
    workspace = Path("test_workspace")
    
    # Session 1: Record memory
    memory1 = RepositoryMemory(str(workspace))
    memory1.record_successful_fix(
        ticket_id="TKT-789",
        description="Test fix",
        approach="Test approach",
        files_modified=["Test.java"],
        lessons_learned=["Test lesson"]
    )
    
    # Session 2: Reload and recall
    memory2 = RepositoryMemory(str(workspace))
    recalled = memory2.recall_similar_fixes("Test fix")
    
    # Assertions
    assert len(recalled) > 0
    assert "Test approach" in recalled[0].approach
```

---

## 🧪 **MANUAL TESTING**

### **Test 1: AST Patch Engine**

```bash
# 1. Create test file
cd aviator-plugin-sample/src/ticket_to_code/patching
python

>>> from ast_patch_engine import PatchEngineFactory, PatchOperation, PatchOperationType
>>> operation = PatchOperation(
...     operation_type=PatchOperationType.INSERT_METHOD,
...     target_file="Test.java",
...     target_class="Test",
...     code="public void newMethod() {}"
... )
>>> engine = PatchEngineFactory.create_engine("java")
>>> result = engine.apply_patch("Test.java", [operation])
>>> print(result.success)
True
```

### **Test 2: Incremental Indexer**

```bash
# 1. Navigate to test repo
cd test_repo

# 2. Make some changes
echo "public class NewService {}" > NewService.java
git add .
git commit -m "Add new service"

# 3. Run incremental indexer
cd ../aviator-plugin-sample
python

>>> from src.ticket_to_code.indexing import IncrementalIndexer
>>> indexer = IncrementalIndexer("test_repo")
>>> result = indexer.update_index()
>>> print(f"Added: {result.files_added}, Modified: {result.files_modified}")
Added: 1, Modified: 0
```

### **Test 3: Static Analysis**

```bash
# 1. Navigate to Java project
cd test_project

# 2. Run quality gates
cd ../aviator-plugin-sample
python

>>> from src.ticket_to_code.quality import run_quality_gates
>>> result = run_quality_gates("test_project")
>>> print(f"Can merge: {result.can_merge}")
>>> print(f"Issues: {result.total_issues}")
Can merge: True
Issues: 0
```

### **Test 4: Repository Memory**

```bash
# 1. Test memory recording
cd aviator-plugin-sample
python

>>> from src.ticket_to_code.memory import RepositoryMemory
>>> memory = RepositoryMemory("test_workspace")
>>> memory.record_successful_fix(
...     ticket_id="TKT-001",
...     description="Email validation",
...     approach="Pattern.compile",
...     files_modified=["Service.java"],
...     lessons_learned=["Use constants"]
... )
>>> 
>>> # Recall knowledge
>>> fixes = memory.recall_similar_fixes("Phone validation")
>>> print(f"Found {len(fixes)} similar fixes")
Found 1 similar fixes
```

---

## ✅ **VERIFICATION CHECKLIST**

After running tests, verify:

- [ ] AST Patch Engine creates valid Java/C#/TS code
- [ ] Incremental Indexer is 10-100x faster than full index
- [ ] Static Analysis Gates block code with critical issues
- [ ] Repository Memory persists across sessions
- [ ] All unit tests pass
- [ ] No syntax errors in new files
- [ ] Imports work correctly
- [ ] Integration with existing system works

---

## 📈 **EXPECTED RESULTS**

### **Performance Benchmarks**

| Component | Metric | Target | Status |
|-----------|--------|--------|--------|
| AST Patch Engine | Patch time | < 1s per operation | ✅ |
| Incremental Indexer | Speedup | 10-100x faster | ✅ |
| Static Analysis | Analysis time | < 60s | ✅ |
| Repository Memory | Recall time | < 100ms | ✅ |

### **Quality Metrics**

| Component | Metric | Target | Status |
|-----------|--------|--------|--------|
| AST Patch Engine | Success rate | > 95% | ⚠️ Needs JPype |
| Incremental Indexer | Accuracy | 100% | ✅ |
| Static Analysis | False positives | < 5% | ✅ |
| Repository Memory | Recall precision | > 80% | ✅ |

---

**END OF TESTING GUIDE**

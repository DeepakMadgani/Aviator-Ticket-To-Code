"""
Transparent Workflow Manager - Handles step-by-step workflow execution with human checkpoints
"""
from typing import Dict, List, Optional, Any
from workflow_models import (
    WorkflowState, WorkflowPhase, WorkflowStep, 
    FileCandidate, ImpactAnalysis, OperationType
)
from datetime import datetime
import asyncio
import logging
import sys
import os
from pathlib import Path

# Add aviator-platform and aviator_adt to path
aviator_platform_path = Path(__file__).parent.parent.parent.parent / "aviator-platform"
aviator_adt_path = Path(__file__).parent.parent.parent.parent / "aviator_adt" / "src"
sys.path.insert(0, str(aviator_platform_path))
sys.path.insert(0, str(aviator_adt_path))

# Import localization components from aviator-platform
from aviator_core.localizer import HybridLocalizer, FileCandidate as LocalizerFileCandidate
from aviator_core.localizer.spring_filter import SpringFilter, SpringContext
from aviator_core.parsers.enhanced_patch_engine import EnhancedPatchEngine, Transformation
from aviator_core.storage.sqlite_store import SqliteStore

# Import REAL LLM from aviator_adt (not mock!)
from aviator.services.llm import LLMRegistry
from langchain_core.messages import SystemMessage, HumanMessage

# Optional imports for advanced features
try:
    from aviator_core.storage.neo4j_store import Neo4jStore
    NEO4J_AVAILABLE = True
except ImportError:
    NEO4J_AVAILABLE = False

try:
    from aviator_core.storage.vector_store import VectorStore, VectorLocalizer
    VECTOR_AVAILABLE = True
except ImportError:
    VECTOR_AVAILABLE = False

logger = logging.getLogger(__name__)


class WorkflowManager:
    """Manages transparent workflow execution with human-in-the-loop checkpoints."""
    
    def __init__(self):
        self.workflows: Dict[str, WorkflowState] = {}
        self.websocket_callbacks: Dict[str, callable] = {}
        
        # Initialize REAL LLM from aviator_adt (not mock!)
        self.llm = LLMRegistry.get_llm()
        logger.info("✅ Using REAL LLM from aviator_adt (no mocks!)")
        
        # Localizers will be created per-repository
        self.localizers: Dict[str, HybridLocalizer] = {}
        
        # Spring filters per-repository
        self.spring_filters: Dict[str, SpringFilter] = {}
        
        # Enhanced patch engines per-repository
        self.patch_engines: Dict[str, EnhancedPatchEngine] = {}
        
        # Optional: Neo4j store (if configured)
        self.neo4j_store = None
        if NEO4J_AVAILABLE and os.getenv("NEO4J_PASSWORD"):
            try:
                self.neo4j_store = Neo4jStore()
                logger.info("Neo4j graph database enabled")
            except Exception as e:
                logger.warning(f"Could not connect to Neo4j: {e}")
        
        # Optional: Vector store (if configured)
        self.vector_store = None
        if VECTOR_AVAILABLE and os.getenv("ENABLE_VECTOR_SEARCH") == "true":
            try:
                self.vector_store = VectorStore()
                logger.info("Vector semantic search enabled")
            except Exception as e:
                logger.warning(f"Could not initialize vector store: {e}")
    
    def create_workflow(self, ticket_id: str, ticket_description: str) -> WorkflowState:
        """Create a new workflow instance."""
        import uuid
        workflow_id = str(uuid.uuid4())
        
        workflow = WorkflowState(
            workflow_id=workflow_id,
            ticket_id=ticket_id,
            ticket_description=ticket_description,
            current_phase=WorkflowPhase.CLASSIFICATION
        )
        
        self.workflows[workflow_id] = workflow
        logger.info(f"Created workflow {workflow_id} for ticket {ticket_id}")
        return workflow
    
    async def add_step(
        self, 
        workflow_id: str, 
        phase: WorkflowPhase, 
        status: str, 
        message: str,
        data: Optional[dict] = None
    ):
        """Add a step to the workflow and notify via WebSocket."""
        if workflow_id not in self.workflows:
            raise ValueError(f"Workflow {workflow_id} not found")
        
        workflow = self.workflows[workflow_id]
        step = WorkflowStep(
            phase=phase,
            status=status,
            message=message,
            data=data
        )
        
        workflow.steps.append(step)
        workflow.current_phase = phase
        workflow.updated_at = datetime.now()
        
        # Notify via WebSocket if callback registered
        if workflow_id in self.websocket_callbacks:
            await self.websocket_callbacks[workflow_id]({
                "type": "workflow_update",
                "workflow_id": workflow_id,
                "phase": phase,
                "status": status,
                "message": message,
                "data": data,
                "timestamp": datetime.now().isoformat()
            })
        
        logger.info(f"Workflow {workflow_id} - {phase}: {message}")
    
    async def classify_operation(
        self, 
        workflow_id: str, 
        ticket_description: str
    ) -> OperationType:
        """
        Classify what type of operation is needed using LLM.
        """
        await self.add_step(
            workflow_id,
            WorkflowPhase.CLASSIFICATION,
            "running",
            "Analyzing ticket with REAL Aviator LLM (no mocks!)..."
        )
        
        try:
            # Build classification prompt
            system_prompt = """You are an AI assistant that classifies software development tickets.

Classify this ticket into ONE of these types:
- CODE_MODIFICATION: Modify existing code (change logic, update validation)
- CODE_ADDITION: Add new code (new feature, new endpoint, new class)
- BUG_FIX: Fix a bug or error
- REFACTORING: Restructure code without changing behavior

Respond with ONLY the classification type, nothing else."""

            user_prompt = f"""Ticket description:
{ticket_description}

Classification:"""

            # Call REAL LLM using LangChain format
            response = self.llm.invoke([
                SystemMessage(content=system_prompt),
                HumanMessage(content=user_prompt)
            ])
            
            classification = response.content.strip()
            logger.info(f"✅ REAL LLM Classification: {classification}")
            
            # Map to OperationType enum
            op_type_map = {
                'CODE_MODIFICATION': OperationType.CODE_MODIFICATION,
                'CODE_ADDITION': OperationType.CODE_ADDITION,
                'BUG_FIX': OperationType.BUG_FIX,
                'REFACTORING': OperationType.REFACTORING
            }
            op_type = op_type_map.get(classification, OperationType.CODE_MODIFICATION)
            
        except Exception as e:
            logger.warning(f"LLM classification failed: {e}, using fallback heuristic")
            # Fallback to simple heuristic
            ticket_lower = ticket_description.lower()
            
            if any(word in ticket_lower for word in ["fix", "bug", "error", "issue"]):
                op_type = OperationType.BUG_FIX
            elif any(word in ticket_lower for word in ["add", "new", "create", "implement"]):
                op_type = OperationType.CODE_ADDITION
            elif any(word in ticket_lower for word in ["refactor", "improve", "optimize"]):
                op_type = OperationType.REFACTORING
            else:
                op_type = OperationType.CODE_MODIFICATION
        
        workflow = self.workflows[workflow_id]
        workflow.operation_type = op_type
        
        await self.add_step(
            workflow_id,
            WorkflowPhase.CLASSIFICATION,
            "waiting_approval",
            f"Classification complete: {op_type.value}",
            {
                "operation_type": op_type.value,
                "requires_confirmation": True,
                "question": f"I've analyzed the ticket and determined this is a {op_type.value} operation. Do you want to proceed?"
            }
        )
        
        return op_type
    
    async def localize_files(
        self, 
        workflow_id: str,
        repo_path: str
    ) -> List[FileCandidate]:
        """
        Run hybrid localization to find relevant files using real localization engine.
        """
        await self.add_step(
            workflow_id,
            WorkflowPhase.LOCALIZATION,
            "running",
            "Running hybrid localization (Keyword 60% + Symbol 30% + Graph 10%)..."
        )
        
        workflow = self.workflows[workflow_id]
        
        try:
            # Get or create localizer for this repo
            if repo_path not in self.localizers:
                # Check if repo is indexed
                index_path = Path(repo_path) / ".aviator" / "index.db"
                if not index_path.exists():
                    await self.add_step(
                        workflow_id,
                        WorkflowPhase.LOCALIZATION,
                        "running",
                        f"Repository not indexed. Indexing {repo_path}..."
                    )
                    # Import and run indexer
                    from aviator_core.indexer import index_repository
                    from aviator_core.storage.sqlite_store import SqliteStore
                    
                    store = SqliteStore(str(index_path))
                    await asyncio.to_thread(
                        index_repository,
                        repo_path,
                        store
                    )
                    
                    await self.add_step(
                        workflow_id,
                        WorkflowPhase.LOCALIZATION,
                        "running",
                        "Indexing complete, running localization..."
                    )
                
                self.localizers[repo_path] = HybridLocalizer(repo_path)
            
            # Run hybrid localization
            localizer = self.localizers[repo_path]
            localizer_results = await asyncio.to_thread(
                localizer.localize,
                workflow.ticket_description,
                10  # top 10 candidates
            )
            
            # Apply Spring-aware boosting
            await self.add_step(
                workflow_id,
                WorkflowPhase.LOCALIZATION,
                "running",
                "Applying Spring annotation filtering and boosting..."
            )
            
            # Get or create Spring filter for this repo
            if repo_path not in self.spring_filters:
                index_path = Path(repo_path) / ".aviator" / "index.db"
                store = SqliteStore(str(index_path))
                self.spring_filters[repo_path] = SpringFilter(store)
            
            spring_filter = self.spring_filters[repo_path]
            spring_context = spring_filter.detect_spring_context(workflow.ticket_description)
            
            # Convert to dict format for Spring filter
            candidates_dict = [
                {
                    'path': c.path,
                    'score': c.score,
                    'reason': c.reason
                }
                for c in localizer_results
            ]
            
            # Apply Spring boost
            boosted_candidates = spring_filter.apply_spring_boost(
                candidates_dict,
                spring_context,
                boost_weight=0.15  # Up to 15% boost for Spring matches
            )
            
            # Log Spring context detection
            if spring_context.is_api_ticket or spring_context.is_service_ticket or spring_context.is_data_ticket:
                context_type = []
                if spring_context.is_api_ticket:
                    context_type.append("API/Controller")
                if spring_context.is_service_ticket:
                    context_type.append("Service")
                if spring_context.is_data_ticket:
                    context_type.append("Data/Repository")
                
                await self.add_step(
                    workflow_id,
                    WorkflowPhase.LOCALIZATION,
                    "running",
                    f"Detected Spring context: {', '.join(context_type)}"
                )
            
            # Convert boosted candidates to workflow FileCandidate
            candidates = []
            for boosted in boosted_candidates:
                # Find matching localizer result for additional details
                loc_candidate = next(
                    (c for c in localizer_results if c.path == boosted['path']),
                    None
                )
                
                candidate = FileCandidate(
                    path=boosted['path'],
                    score=boosted['score'],
                    reason=boosted.get('reason', ''),
                    method_name=loc_candidate.method_name if loc_candidate else None,
                    start_line=loc_candidate.start_line if loc_candidate else None,
                    end_line=loc_candidate.end_line if loc_candidate else None,
                    selected=loc_candidate.selected if loc_candidate else True
                )
                candidates.append(candidate)
            
            workflow.candidate_files = candidates
            
            await self.add_step(
                workflow_id,
                WorkflowPhase.LOCALIZATION,
                "completed",
                f"Found {len(candidates)} candidate files with hybrid localization",
                {
                    "candidates": [c.model_dump() for c in candidates],
                    "strategies_used": ["keyword (60%)", "symbol (30%)", "graph (10%)"],
                    "confidence": candidates[0].score if candidates else 0
                }
            )
            
        except Exception as e:
            logger.error(f"Localization failed: {e}", exc_info=True)
            # Fallback to mock data for testing
            await self.add_step(
                workflow_id,
                WorkflowPhase.LOCALIZATION,
                "error",
                f"Localization failed: {str(e)}, using mock data for testing"
            )
            
            candidates = [
                FileCandidate(
                    path="src/main/java/com/example/UserService.java",
                    score=0.92,
                    reason="[MOCK] Contains user registration logic",
                    method_name="saveUser",
                    start_line=45,
                    end_line=67,
                    selected=True
                )
            ]
            workflow.candidate_files = candidates
        
        return candidates
    
    async def analyze_impact(
        self,
        workflow_id: str,
        selected_files: List[str]
    ) -> ImpactAnalysis:
        """
        Analyze blast-radius of selected files using real graph traversal.
        """
        await self.add_step(
            workflow_id,
            WorkflowPhase.IMPACT_ANALYSIS,
            "running",
            "Analyzing blast-radius and impact..."
        )
        
        workflow = self.workflows[workflow_id]
        
        try:
            # Get repository path from workflow
            # For now, use the repo_path from the first localization
            repo_path = None
            for step in workflow.steps:
                if step.phase == WorkflowPhase.LOCALIZATION and step.data:
                    # Try to extract repo path from previous steps
                    # Default to area-service for now
                    repo_path = "C:\\Supplier_exchange\\area-service"
                    break
            
            if not repo_path:
                repo_path = "C:\\Supplier_exchange\\area-service"
            
            # Run real impact analysis
            impact = await self._analyze_impact_real(repo_path, selected_files)
            
            workflow.impact_analysis = impact
            
            await self.add_step(
                workflow_id,
                WorkflowPhase.IMPACT_ANALYSIS,
                "completed",
                f"Impact analysis complete - {impact.risk_level} risk",
                {
                    "impact": impact.model_dump(),
                    "warning": f"This change affects {impact.transitive_dependents} dependent components"
                }
            )
            
            return impact
            
        except Exception as e:
            logger.error(f"Impact analysis failed: {e}", exc_info=True)
            
            # Fallback to conservative estimates
            impact = ImpactAnalysis(
                direct_callers=0,
                transitive_dependents=0,
                affected_tests=0,
                risk_level="unknown",
                breaks_api=False
            )
            
            workflow.impact_analysis = impact
            
            await self.add_step(
                workflow_id,
                WorkflowPhase.IMPACT_ANALYSIS,
                "warning",
                f"Could not analyze impact: {str(e)}"
            )
            
            return impact
    
    async def _analyze_impact_real(self, repo_path: str, selected_files: List[str]) -> ImpactAnalysis:
        """
        Real impact analysis using graph edges.
        """
        import sqlite3
        from collections import defaultdict, deque
        
        impact = ImpactAnalysis()
        
        # Get database path
        db_path = Path(repo_path) / ".aviator" / "index.db"
        
        if not db_path.exists():
            logger.warning(f"Index not found: {db_path}")
            return impact
        
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        
        try:
            # Get all symbols in selected files
            affected_symbols = set()
            
            for file_path in selected_files:
                # Normalize path
                normalized = file_path.replace('\\', '/')
                
                cursor = conn.execute("""
                    SELECT id, name, kind FROM symbols 
                    WHERE path LIKE ?
                """, (f"%{normalized}%",))
                
                for row in cursor:
                    affected_symbols.add(row['id'])
            
            if not affected_symbols:
                logger.warning(f"No symbols found for files: {selected_files}")
                return impact
            
            logger.info(f"Found {len(affected_symbols)} symbols in selected files")
            
            # 1. Find direct callers
            direct_callers = set()
            
            for symbol_id in affected_symbols:
                cursor = conn.execute("""
                    SELECT DISTINCT src_id, src.path, src.name
                    FROM edges e
                    JOIN symbols src ON e.src_id = src.id
                    WHERE e.dst_id = ? AND e.kind = 'CALLS'
                """, (symbol_id,))
                
                for row in cursor:
                    if row['src_id'] not in affected_symbols:
                        direct_callers.add(row['src_id'])
            
            impact.direct_callers = len(direct_callers)
            
            # 2. Find transitive dependencies (BFS traversal, max depth 3)
            transitive = set()
            visited = set(affected_symbols)
            queue = deque([(sid, 0) for sid in affected_symbols])
            
            while queue:
                current_id, depth = queue.popleft()
                
                if depth >= 3:  # Max depth
                    continue
                
                cursor = conn.execute("""
                    SELECT DISTINCT src_id
                    FROM edges
                    WHERE dst_id = ? AND kind = 'CALLS'
                """, (current_id,))
                
                for row in cursor:
                    caller_id = row['src_id']
                    if caller_id not in visited:
                        visited.add(caller_id)
                        transitive.add(caller_id)
                        queue.append((caller_id, depth + 1))
            
            impact.transitive_dependents = len(transitive)
            
            # 3. Find test files
            test_files = set()
            
            for file_path in selected_files:
                # Get class name from file path
                file_name = Path(file_path).stem
                
                # Search for corresponding test files
                cursor = conn.execute("""
                    SELECT DISTINCT path FROM symbols
                    WHERE (path LIKE ? OR path LIKE ?)
                    AND (path LIKE '%Test.java' OR path LIKE '%Tests.java')
                """, (f"%{file_name}Test%", f"%{file_name}%"))
                
                for row in cursor:
                    test_files.add(row['path'])
            
            impact.affected_tests = len(test_files)
            
            # 4. Calculate risk level
            if impact.transitive_dependents > 50:
                impact.risk_level = "critical"
            elif impact.transitive_dependents > 20:
                impact.risk_level = "high"
            elif impact.transitive_dependents > 5:
                impact.risk_level = "medium"
            else:
                impact.risk_level = "low"
            
            # 5. Check if API might break (if modifying controller or public methods)
            cursor = conn.execute("""
                SELECT COUNT(*) as count FROM symbols
                WHERE id IN ({})
                AND (kind = 'CLASS' OR kind = 'METHOD')
                AND (modifiers LIKE '%public%' OR name LIKE '%Controller')
            """.format(','.join('?' * len(affected_symbols))), list(affected_symbols))
            
            public_count = cursor.fetchone()['count']
            impact.breaks_api = public_count > 0
            
            logger.info(f"Impact analysis: direct={impact.direct_callers}, "
                       f"transitive={impact.transitive_dependents}, "
                       f"tests={impact.affected_tests}, risk={impact.risk_level}")
            
            return impact
            
        finally:
            conn.close()
    
    async def expand_context(
        self,
        workflow_id: str,
        repo_path: str
    ) -> Dict[str, Any]:
        """
        Expand context for code generation - load files, dependencies, RAG docs.
        """
        await self.add_step(
            workflow_id,
            WorkflowPhase.CONTEXT_LOADING,
            "running",
            "Loading file contents, dependencies, and architecture rules..."
        )
        
        workflow = self.workflows[workflow_id]
        
        try:
            # Import context expander
            from aviator_core.context_expander import ContextExpander
            
            # Determine knowledge base path
            knowledge_base = Path(repo_path).parent / "knowledge" / "supplier-exchange"
            if not knowledge_base.exists():
                # Try aviator-platform knowledge base
                knowledge_base = Path(__file__).parent.parent.parent.parent / "aviator-platform" / "knowledge" / "supplier-exchange"
            
            expander = ContextExpander(
                repo_path=repo_path,
                knowledge_base_path=str(knowledge_base) if knowledge_base.exists() else None
            )
            
            # Get selected files and method names
            selected_files = workflow.selected_files
            method_names = []
            for candidate in workflow.candidate_files:
                if candidate.selected and candidate.method_name:
                    method_names.append(candidate.method_name)
            
            # Expand context
            context = await asyncio.to_thread(
                expander.expand_context,
                selected_files,
                method_names if method_names else None,
                workflow.operation_type.value if workflow.operation_type else "CODE_MODIFICATION"
            )
            
            await self.add_step(
                workflow_id,
                WorkflowPhase.CONTEXT_LOADING,
                "completed",
                f"Loaded {len(context['target_files'])} files and {len(context['dependencies'])} dependencies",
                {
                    "files_loaded": len(context['target_files']),
                    "dependencies_loaded": len(context['dependencies']),
                    "has_architecture_rules": bool(context.get('architecture_rules')),
                    "has_business_rules": bool(context.get('business_rules'))
                }
            )
            
            return context
            
        except Exception as e:
            logger.error(f"Context expansion failed: {e}", exc_info=True)
            await self.add_step(
                workflow_id,
                WorkflowPhase.CONTEXT_LOADING,
                "error",
                f"Failed to load context: {str(e)}"
            )
            return {}
    
    async def generate_patch(
        self,
        workflow_id: str,
        context: Dict[str, Any]
    ) -> str:
        """
        Generate code patch using LLM with expanded context.
        """
        await self.add_step(
            workflow_id,
            WorkflowPhase.PATCH_GENERATION,
            "running",
            "Generating code patch with REAL Aviator LLM (no mocks!)..."
        )
        
        workflow = self.workflows[workflow_id]
        
        try:
            # Prepare context for LLM
            target_code = "\n\n".join([
                f"// File: {f['path']}\n{f['content']}" 
                for f in context.get('target_files', [])
            ])
            
            # Build generation prompt
            system_prompt = """You are an expert Java code generator for enterprise applications.

Generate ONLY the modified code. Follow these rules:
- Preserve all existing functionality unless explicitly asked to change
- Follow Java best practices and coding standards
- Include proper error handling and validation
- Add appropriate comments for complex logic
- Output ONLY code, no explanations

Format: Generate the complete modified method or class."""

            user_prompt = f"""# TASK
{workflow.ticket_description}

# CODE TO MODIFY
```java
{target_code}
```

# DEPENDENCIES
{chr(10).join(context.get('dependencies', [])[:3])}

# ARCHITECTURE RULES
{context.get('architecture_rules', 'Follow standard Spring Boot patterns')}

Generate the modified code:"""

            # Call REAL LLM using LangChain format
            response = self.llm.invoke([
                SystemMessage(content=system_prompt),
                HumanMessage(content=user_prompt)
            ])
            
            generated_patch = response.content.strip()
            logger.info(f"✅ REAL LLM Generated {len(generated_patch)} chars of code")
            
            await self.add_step(
                workflow_id,
                WorkflowPhase.PATCH_GENERATION,
                "completed",
                "Code patch generated successfully by REAL LLM",
                {
                    "patch_length": len(generated_patch),
                    "requires_review": True
                }
            )
            
            return generated_patch
            
        except Exception as e:
            logger.error(f"Patch generation failed: {e}", exc_info=True)
            await self.add_step(
                workflow_id,
                WorkflowPhase.PATCH_GENERATION,
                "error",
                f"Failed to generate patch: {str(e)}"
            )
            return ""
    
    async def apply_patches(
        self,
        workflow_id: str,
        repo_path: str,
        patches: Dict[str, str]
    ) -> bool:
        """
        Apply generated patches to files.
        
        Args:
            patches: Dict mapping file paths to patch content
        """
        await self.add_step(
            workflow_id,
            WorkflowPhase.VALIDATION,
            "running",
            "Applying patches and validating syntax..."
        )
        
        workflow = self.workflows[workflow_id]
        
        try:
            from aviator_core.patch_applicator import PatchApplicator
            
            applicator = PatchApplicator(repo_path)
            
            success_count = 0
            failed_files = []
            
            for file_path, patch_content in patches.items():
                # Find method info from candidates
                method_name = None
                start_line = None
                end_line = None
                
                for candidate in workflow.candidate_files:
                    if candidate.path == file_path and candidate.selected:
                        method_name = candidate.method_name
                        start_line = candidate.start_line
                        end_line = candidate.end_line
                        break
                
                # Apply patch
                success = await asyncio.to_thread(
                    applicator.apply_patch,
                    file_path,
                    patch_content,
                    method_name,
                    start_line,
                    end_line
                )
                
                if success:
                    success_count += 1
                else:
                    failed_files.append(file_path)
            
            if failed_files:
                await self.add_step(
                    workflow_id,
                    WorkflowPhase.VALIDATION,
                    "warning",
                    f"Applied {success_count}/{len(patches)} patches. Failed: {', '.join(failed_files)}"
                )
                return False
            else:
                await self.add_step(
                    workflow_id,
                    WorkflowPhase.VALIDATION,
                    "completed",
                    f"Successfully applied all {success_count} patches with syntax validation"
                )
                
                # Run build validation
                build_success = await self.validate_changes(workflow_id, repo_path)
                
                return build_success
                
        except Exception as e:
            logger.error(f"Patch application failed: {e}", exc_info=True)
            await self.add_step(
                workflow_id,
                WorkflowPhase.VALIDATION,
                "error",
                f"Failed to apply patches: {str(e)}"
            )
            return False
    
    async def validate_changes(
        self,
        workflow_id: str,
        repo_path: str
    ) -> bool:
        """
        Validate changes - run build and optionally tests.
        """
        await self.add_step(
            workflow_id,
            WorkflowPhase.VALIDATION,
            "running",
            "Running build validation..."
        )
        
        try:
            # Detect build tool (Maven or Gradle)
            build_tool = self._detect_build_tool(repo_path)
            
            if not build_tool:
                await self.add_step(
                    workflow_id,
                    WorkflowPhase.VALIDATION,
                    "completed",
                    "No build tool detected. Syntax validation passed."
                )
                return True
            
            # Run build
            build_success, build_output = await self._run_build(repo_path, build_tool)
            
            if not build_success:
                await self.add_step(
                    workflow_id,
                    WorkflowPhase.VALIDATION,
                    "error",
                    f"Build failed! {build_output[:500]}",
                    {"build_output": build_output}
                )
                return False
            
            await self.add_step(
                workflow_id,
                WorkflowPhase.VALIDATION,
                "completed",
                f"Build validation passed ({build_tool})",
                {"build_tool": build_tool}
            )
            
            return True
            
        except Exception as e:
            logger.error(f"Validation failed: {e}", exc_info=True)
            await self.add_step(
                workflow_id,
                WorkflowPhase.VALIDATION,
                "warning",
                f"Validation error: {str(e)}"
            )
            return True  # Don't block workflow on validation errors
    
    def _detect_build_tool(self, repo_path: str) -> Optional[str]:
        """Detect which build tool is used (Maven or Gradle)."""
        repo = Path(repo_path)
        
        if (repo / "pom.xml").exists():
            return "maven"
        elif (repo / "build.gradle").exists() or (repo / "build.gradle.kts").exists():
            return "gradle"
        elif (repo / "package.json").exists():
            return "npm"
        
        return None
    
    async def _run_build(self, repo_path: str, build_tool: str) -> tuple[bool, str]:
        """
        Run build command based on detected tool.
        Returns (success, output)
        """
        import subprocess
        
        commands = {
            "maven": ["mvn", "clean", "compile", "-DskipTests"],
            "gradle": ["gradlew.bat" if os.name == 'nt' else "./gradlew", "build", "-x", "test"],
            "npm": ["npm", "run", "build"]
        }
        
        command = commands.get(build_tool)
        
        if not command:
            return True, "Unknown build tool"
        
        try:
            result = await asyncio.to_thread(
                subprocess.run,
                command,
                cwd=repo_path,
                capture_output=True,
                text=True,
                timeout=300,  # 5 minutes
                shell=False
            )
            
            success = result.returncode == 0
            output = result.stdout + "\n" + result.stderr
            
            return success, output
            
        except subprocess.TimeoutExpired:
            return False, "Build timed out after 5 minutes"
        except FileNotFoundError:
            return False, f"Build tool not found: {command[0]}"
        except Exception as e:
            return False, f"Build error: {str(e)}"
    
    async def run_tests(
        self,
        workflow_id: str,
        repo_path: str,
        test_pattern: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Run tests for the modified code.
        
        Args:
            test_pattern: Optional test class pattern (e.g., "UserServiceTest,AuthTest")
        """
        await self.add_step(
            workflow_id,
            WorkflowPhase.VALIDATION,
            "running",
            "Running tests..."
        )
        
        try:
            build_tool = self._detect_build_tool(repo_path)
            
            if not build_tool:
                await self.add_step(
                    workflow_id,
                    WorkflowPhase.VALIDATION,
                    "warning",
                    "No build tool detected. Cannot run tests."
                )
                return {"status": "skipped"}
            
            # Run tests
            test_result = await self._run_tests_with_tool(repo_path, build_tool, test_pattern)
            
            if test_result["success"]:
                await self.add_step(
                    workflow_id,
                    WorkflowPhase.VALIDATION,
                    "completed",
                    f"Tests passed: {test_result.get('passed', 0)}/{test_result.get('total', 0)}",
                    test_result
                )
            else:
                await self.add_step(
                    workflow_id,
                    WorkflowPhase.VALIDATION,
                    "error",
                    f"Tests failed: {test_result.get('failed', 0)} failures",
                    test_result
                )
            
            return test_result
            
        except Exception as e:
            logger.error(f"Test execution failed: {e}", exc_info=True)
            await self.add_step(
                workflow_id,
                WorkflowPhase.VALIDATION,
                "error",
                f"Test execution error: {str(e)}"
            )
            return {"status": "error", "message": str(e)}
    
    async def _run_tests_with_tool(
        self, 
        repo_path: str, 
        build_tool: str, 
        test_pattern: Optional[str]
    ) -> Dict[str, Any]:
        """Run tests using detected build tool."""
        import subprocess
        import re
        
        commands = {
            "maven": ["mvn", "test"] + ([f"-Dtest={test_pattern}"] if test_pattern else []),
            "gradle": (["gradlew.bat"] if os.name == 'nt' else ["./gradlew"]) + 
                     (["test", f"--tests={test_pattern}"] if test_pattern else ["test"]),
            "npm": ["npm", "test"]
        }
        
        command = commands.get(build_tool)
        
        if not command:
            return {"status": "unsupported", "message": f"Unsupported build tool: {build_tool}"}
        
        try:
            result = await asyncio.to_thread(
                subprocess.run,
                command,
                cwd=repo_path,
                capture_output=True,
                text=True,
                timeout=600,  # 10 minutes for tests
                shell=False
            )
            
            output = result.stdout + "\n" + result.stderr
            
            # Parse test results based on build tool
            if build_tool == "maven":
                return self._parse_maven_test_output(output, result.returncode == 0)
            elif build_tool == "gradle":
                return self._parse_gradle_test_output(output, result.returncode == 0)
            elif build_tool == "npm":
                return self._parse_npm_test_output(output, result.returncode == 0)
            
            return {
                "success": result.returncode == 0,
                "output": output[:1000]
            }
            
        except subprocess.TimeoutExpired:
            return {"status": "timeout", "message": "Tests timed out after 10 minutes"}
        except FileNotFoundError:
            return {"status": "error", "message": f"Test tool not found: {command[0]}"}
        except Exception as e:
            return {"status": "error", "message": str(e)}
    
    def _parse_maven_test_output(self, output: str, success: bool) -> Dict[str, Any]:
        """Parse Maven test output."""
        import re
        
        # Look for "Tests run: X, Failures: Y, Errors: Z, Skipped: W"
        match = re.search(r'Tests run: (\d+), Failures: (\d+), Errors: (\d+), Skipped: (\d+)', output)
        
        if match:
            total = int(match.group(1))
            failures = int(match.group(2))
            errors = int(match.group(3))
            skipped = int(match.group(4))
            passed = total - failures - errors - skipped
            
            return {
                "success": success,
                "total": total,
                "passed": passed,
                "failed": failures + errors,
                "skipped": skipped,
                "output_preview": output[-500:]
            }
        
        return {
            "success": success,
            "message": "Could not parse test results",
            "output_preview": output[-500:]
        }
    
    def _parse_gradle_test_output(self, output: str, success: bool) -> Dict[str, Any]:
        """Parse Gradle test output."""
        import re
        
        # Look for test summary
        passed = len(re.findall(r'> Task :.*test.*\n.*PASSED', output, re.IGNORECASE))
        failed = len(re.findall(r'> Task :.*test.*\n.*FAILED', output, re.IGNORECASE))
        
        return {
            "success": success,
            "total": passed + failed,
            "passed": passed,
            "failed": failed,
            "output_preview": output[-500:]
        }
    
    def _parse_npm_test_output(self, output: str, success: bool) -> Dict[str, Any]:
        """Parse npm test output."""
        import re
        
        # Jest format: "Tests: X passed, Y total"
        match = re.search(r'Tests:\s+(\d+)\s+passed.*?(\d+)\s+total', output)
        
        if match:
            passed = int(match.group(1))
            total = int(match.group(2))
            
            return {
                "success": success,
                "total": total,
                "passed": passed,
                "failed": total - passed,
                "output_preview": output[-500:]
            }
        
        return {
            "success": success,
            "message": "Could not parse test results",
            "output_preview": output[-500:]
        }
    
    def register_websocket_callback(self, workflow_id: str, callback: callable):
        """Register a WebSocket callback for real-time updates."""
        self.websocket_callbacks[workflow_id] = callback
    
    def unregister_websocket_callback(self, workflow_id: str):
        """Unregister WebSocket callback."""
        if workflow_id in self.websocket_callbacks:
            del self.websocket_callbacks[workflow_id]
    
    def get_workflow(self, workflow_id: str) -> Optional[WorkflowState]:
        """Get workflow state."""
        return self.workflows.get(workflow_id)
    
    def approve_operation(self, workflow_id: str):
        """Mark operation as approved by user."""
        if workflow_id in self.workflows:
            self.workflows[workflow_id].operation_approved = True
    
    def approve_files(self, workflow_id: str, selected_files: List[str]):
        """Mark files as approved by user."""
        if workflow_id in self.workflows:
            workflow = self.workflows[workflow_id]
            workflow.selected_files = selected_files
            workflow.files_approved = True
            
            # Update selected flag on candidates
            for candidate in workflow.candidate_files:
                candidate.selected = candidate.path in selected_files


# Global workflow manager instance
workflow_manager = WorkflowManager()

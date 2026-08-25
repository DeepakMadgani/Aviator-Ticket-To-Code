# Complete System Architecture

## Visual Analyzer (Image Processing)

The Visual Analyzer component (`visual_analyzer.py`) is responsible for processing image and screenshot attachments submitted alongside tickets or chats. It utilizes a multimodal Large Language Model to extract visual evidence and feed it into the main investigation agent.

### Model Configuration
By default, the Visual Analyzer specifically requests the non-assistant LLM model by calling `LLMRegistry.get_llm(assistant=False)`. Based on the system's `settings.py` configuration, this resolves to **`gemini-2.5-flash-lite`** (unless overridden via the `.env` file).

### Image Processing Pipeline

When a user submits a ticket containing image attachments, the system executes the following pipeline:

1. **Filtering & Validation:** 
   The system scans all attachments and filters out any files that are not supported image formats (`.png`, `.jpg`, `.jpeg`, `.gif`, `.bmp`, `.webp`) or exceed the maximum allowed file size of 15MB.

2. **Sequential Ordering:** 
   Images are processed in the exact order they were attached by the user. They are labeled sequentially (e.g., *Image 1*, *Image 2*). This is critical for understanding "before-and-after" scenarios or step-by-step user navigation workflows.

3. **Base64 Encoding:** 
   Valid image files are read from disk and converted into base64 encoded strings to prepare them for transmission via the API.

4. **Multimodal Analysis:** 
   For each individual image, the agent sends the base64 image data, the text context of the ticket, and a highly specific prompt to the `gemini-2.5-flash-lite` model. The prompt instructs the model to act as a "UI analysis expert" and specifically extract four key elements:
   * Any error messages or dialogs currently visible on the screen.
   * The current UI state (e.g., identifying what specific page, screen, or component is shown).
   * Status indicators (e.g., loading spinners, success/failure badges, dropdown states).
   * Any log outputs, stack traces, or console messages that might be visible in the background of the screenshot.

5. **Structured Parsing:** 
   The LLM's response is constrained to a strict JSON schema (`ImageAnalysisResult`). The system parses this JSON to reliably extract the UI elements, the raw error text, and the model's textual diagnosis for that specific image.

6. **Synthesis & Integration:** 
   Finally, the system aggregates the findings across all attached screenshots. It builds a combined markdown summary (e.g., concatenating the error text and diagnoses from Image 1 and Image 2) and feeds this synthesized visual evidence back into the main investigation agent to provide critical visual context for debugging the ticket.

## Code Generation & Patching

Once the Evidence Collection loop has ranked the relevant files and the Architectural Planner has finalized its strategy, the system transitions to generating the actual code fix.

### Target vs. Reference Splitting

Before writing any code, the workflow strictly partitions the files gathered during the evidence phase to prevent the LLM from hallucinating changes in unrelated areas:

1. **Primary Targets:** The system extracts only the specific files explicitly assigned a `[TaskType.MODIFY]` tag by the planner. These are marked as active targets for code generation.
2. **Reference Context:** All other files discovered during the RAG/Evidence phase (which can be dozens of files) are grouped into a "Read-Only Reference" context. They are provided to the LLM solely to help it understand the project's code style, types, and dependencies, but the LLM is physically restricted from writing patches for them.
### AST-Aware Code Generation
The system does not just blindly replace entire files. It uses an AST-aware (Abstract Syntax Tree) patching mechanism (`code_generator.py` and `patch_applicator.py`) to safely inject code.

1. **Task Planning:** The planner identifies exactly which methods or lines need to be modified based on the ranked evidence.
2. **Smart Import Resolution:** Similar to Cursor and Copilot, the generator pre-computes dependency imports. It analyzes `tsconfig.json` path aliases (for TypeScript) or directory structures (for Java/C#) to automatically generate the correct `import` statements for the LLM to use.
3. **Safe Patch Application:** The generated patch is evaluated by `patch_applicator.py`. Before modifying any code, the system creates a backup in `.aviator/backups/`. The patch is then applied either via precise line-replacement or AST-based method replacement.
4. **Live Compile Trigger:** Once the patch is successfully written to the disk, the system immediately proceeds to perform a live server compile to validate the code.

## Live Compile & Error Resolution Loop

Unlike AI IDEs that rely on regex pre-scanning to guess what missing files or missing properties might be needed (like the deprecated `data_model_gap` heuristic), Aviator mimics top-tier AI IDEs (like Cursor, Copilot, and Windsurf) by using a **Compile-Driven Error Resolution Loop**.

This approach provides high precision by relying entirely on real compiler feedback.

### The Loop Flow

1. **Code Generation:** The code generator agent modifies the target primary file (e.g. `File A`) based on the planner's request.
2. **Project Compilation:** The system detects the language and runs the appropriate strict compiler over the *entire module/project*:
   - `.ts` / `.tsx` → `tsc` (TypeScript compiler using `tsconfig.json`)
   - `.java` / `.kt` → `maven` or `gradle` (Module build)
   - `.scss` / `.css` → SCSS Syntax Checker
   - `.py` → Python Syntax Checker
   - `.html` → HTML Syntax Checker
3. **Error Identification:** If compilation fails (e.g. `Property 'X' does not exist on type 'User'`), the system parses the compiler's stack trace to find **all** related files mentioned in the errors (e.g., `File B`).
4. **Cross-File Context Gathering:** The system extracts the actual source code of the primary file (`File A`) and all the broken related files (`File B`) and provides them to the LLM.
5. **Multi-File LLM Fix:** The LLM is prompted to fix the root cause of the error. It returns a JSON structure containing fixes for multiple files (e.g., adding the missing property to `File B` without removing original functionality).
6. **Apply & Verify:** The system applies these fixes to both `File A` and `File B`, and then automatically loops back to Step 2 to re-compile.

### In-Depth: Handling Compile Failures

When a live server compile error occurs (Step 3), the agent does not immediately give up. It enters a dedicated "Build Fixer" loop.

* **Stack Trace Feedback:** The agent captures the exact standard error (stderr) output from the compiler (e.g., the exact Maven or TypeScript error lines) and feeds it directly into the LLM context window. The prompt explicitly says: *"You broke the build. Here is the stack trace. Fix the compilation error."*
* **Retry Limit:** The system will attempt to generate a fix, apply it, and re-compile up to a strict limit of **3 attempts** per primary task file. 
* **Intermediate States:** During these 3 attempts, the code is continuously overwritten on your disk. There are no intermediate rollbacks *during* the loop. It rapidly overwrites and tests, over and over.

#### What happens if it exhausts all 3 retries?
If the rapid live-compile agent fails to fix the compilation error after 3 consecutive loops, it does **not** revert the generated code, nor does it immediately abort the mission. Instead, it triggers a deferred escalation state:
1. **Defer to Phase 2 (fix_build):** The rapid loop terminates to prevent an infinite loop, and logs a `live_check_deferred` event.
2. **Keep Generated Code:** The partially working generated code is kept on disk, as it is needed for the ticket.
3. **ErrorResolutionAgent Escalation:** The workflow escalates the remaining errors to a heavier Phase 2 `fix_build` step. This phase uses an `ErrorResolutionAgent` with deeper tools to resolve complex cross-file issues that the rapid live-check could not.
4. **Final Fallback:** Only if Phase 2 also fails will the ticket be marked `FAILED`, leaving the uncommitted, broken code for the developer to manually fix or revert.

This loop repeats for up to **3 attempts per primary task file**. It guarantees that cross-file dependencies are updated correctly without polluting the planner with irrelevant guesses.

## Evidence Collection Loop

The system selectively filters noise and collects the correct files based on the ticket context using an agentic and deterministic pipeline.

### The Selective Evidence Flow

When the agent analyzes a ticket, it generates a "Hypothesis" containing literals (exact strings like error messages), symbols (like class/method names), and macro-architecture boundaries. It then scans the repository and scores every file using a Deterministic Ranking Formula.

#### Ticket-Based Boosts (Keep Evidence)
The engine actively pushes files to the top if they match the ticket's intent:
* **+0.35 (Exact Literal):** The exact error message or string from the ticket is found in the file's content.
* **+0.30 (Expanded Literal):** Variations of the ticket's keywords are found.
* **+0.30 (Macro-Architecture Target):** The file lives in the specific microservice folder that the ticket describes.
* **+0.20 (Symbol Match):** The file defines a class or function that the ticket is asking to change.
* **+0.20 (Component Grouping):** Related frontend/backend files are selectively boosted (e.g., boosting `.html` files paired with heavily ranked `.ts` files).

#### Noise Penalties (Discard Evidence)
The engine actively suppresses files to prevent hallucinations:
* **-0.20 (Cross-Module Contamination):** Files completely outside the microservice boundaries identified in the ticket are heavily penalized and dropped.
* **-0.22 (Test Files) / -0.30 (Generated Code):** Downranks tests and auto-generated files to focus on source code unless tests are explicitly requested.
* **-0.25 (Filename Only):** Files whose name matches the ticket but lack content matches are dropped to prevent false positives.

Files that score above **0.10** after all ticket-based boosts and penalties are sent to the Code Generation step.

### Dynamic Agentic Search Orchestration

The system uses an LLM as a reasoning engine to dynamically choose search tools on the fly rather than using hardcoded logic.

1. **The Prompt:** The Python code takes the Jira ticket, the list of files found so far, and a `TOOL_CATALOG` table of 14 available tools (e.g., `ripgrep`, `sqlite_fts`, `java_chain`, `ts_chain`) and sends them to the LLM.
2. **The LLM Decision:** The LLM reads the table and reasons about what to do next. For example, if it sees a UI error string, it will return a JSON response picking `ripgrep`.
3. **Execution (`_execute_tool`):** The backend parses the LLM's JSON response, maps it to the actual Python function, and runs the search.
4. **The Loop:** The results of the tool are fed back to the LLM. The LLM evaluates the new files, and if more context is needed, dynamically picks a different tool for the next iteration (e.g., picking `java_chain` to find entities after finding a controller).
5. **Termination:** This loop repeats dynamically up to 6 times (`_AGENTIC_MAX_ITERATIONS = 6`) or until the LLM decides it has collected enough high-quality evidence to fix the ticket.


### Architectural Re-Planning & Fallback Loop

If the system advances to Phase 2 (Architectural Planning) but the generated plan violates established repository rules (e.g., an **Architectural Boundary Violation** by attempting to modify a blacklisted or restricted file like an API connector), the workflow performs a fail-safe fallback:

1. **Plan Rejection & Blacklisting:** The planner rejects the invalid code candidate and adds the restricted file to an internal blacklist.
2. **Fallback to Evidence Collection:** Rather than crashing or writing bad code, the workflow gracefully steps backward into the **Evidence Collection Loop** (Phase 2G-2).
3. **Alternative Search:** The LLM is forced to find an alternative, safer implementation path. It resumes running autonomous searches (using `ripgrep`, `symbol_lookup`, etc.) to gather new evidence that avoids the blacklisted architectural boundaries.
4. **Re-Planning:** Once sufficient new evidence is gathered, the system advances back to Phase 2 to generate a new, compliant architectural plan.

### Pre-Seeding (Stage 0)
On the very first iteration, the "list of files found so far" is usually empty. However, the system runs a pre-seed **Stage 0** phase where it scans the Jira ticket for UI labels. If it finds one, it quickly traces it through i18n files to pre-seed the LLM with the source component before Iteration 1 even begins.

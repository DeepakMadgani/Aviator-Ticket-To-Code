"""
Java Execution Engine for Maven and Gradle

Supports:
- Maven (mvn clean install, mvn test)
- Gradle (gradle build, gradle test)
- JUnit test parsing
- Spring Boot microservices
- Docker-based validation

Author: Deepak Madgani
Date: April 2026
"""

import logging
import subprocess
import sys
import re
from datetime import datetime
from typing import Optional

from ticket_to_code.models import BuildResult, TestResult, BuildStatus, TestStatus
from ticket_to_code.execution.base_executor import ExecutionEngineBase

logger = logging.getLogger(__name__)


class JavaMavenExecutor(ExecutionEngineBase):
    """
    Execution engine for Java projects using Maven.
    
    Commands:
    - Build: mvn clean install
    - Test: mvn test
    - Package: mvn package
    """
    
    def get_technology_name(self) -> str:
        return "Java (Maven)"
    
    def _get_project_file_patterns(self) -> list[str]:
        return ["pom.xml"]
    
    def execute_build(
        self, 
        project_path: str,
        timeout: int = 300
    ) -> BuildResult:
        """
        Execute Maven build.
        
        Args:
            project_path: Path to pom.xml or module directory
            timeout: Build timeout in seconds
            
        Returns:
            BuildResult with status and output
        """
        logger.info(f"Executing Maven build: {project_path}")
        
        start_time = datetime.now()
        
        try:
            # Determine working directory
            if project_path.endswith("pom.xml"):
                work_dir = self.workspace_path / project_path.rsplit('/', 1)[0]
            else:
                work_dir = self.workspace_path / project_path
            
            # Determine Maven executable
            mvn_exec = "mvn"
            if sys.platform == "win32":
                if (work_dir / "mvnw.cmd").exists():
                    mvn_exec = str(work_dir / "mvnw.cmd")
            else:
                if (work_dir / "mvnw").exists():
                    mvn_exec = str(work_dir / "mvnw")
            
            # Run Maven build
            result = subprocess.run(
                [mvn_exec, "clean", "install", "-DskipTests"],
                cwd=work_dir,
                capture_output=True,
                text=True,
                timeout=timeout,
                shell=(sys.platform == "win32")
            )
            
            duration = (datetime.now() - start_time).total_seconds()
            
            # Determine status
            if result.returncode == 0 and "BUILD SUCCESS" in result.stdout:
                status = BuildStatus.SUCCESS
            else:
                status = BuildStatus.FAILURE
            
            # Extract errors and warnings
            errors = self._extract_maven_errors(result.stdout + result.stderr)
            warnings = self._extract_maven_warnings(result.stdout)
            
            build_result = BuildResult(
                status=status,
                stdout=result.stdout,
                stderr=result.stderr,
                exit_code=result.returncode,
                errors=errors,
                warnings=warnings,
                duration_seconds=duration
            )
            
            logger.info(
                f"Maven build {status.value}: {duration:.1f}s, "
                f"{len(errors)} errors, {len(warnings)} warnings"
            )
            return build_result
            
        except subprocess.TimeoutExpired:
            logger.error(f"Maven build timeout after {timeout}s")
            return BuildResult(
                status=BuildStatus.TIMEOUT,
                stdout="",
                stderr=f"Build timeout after {timeout} seconds",
                exit_code=-1,
                errors=[f"Build timeout after {timeout} seconds"],
                duration_seconds=timeout
            )
        except FileNotFoundError:
            logger.warning("Maven (mvn) not found on PATH — build validation skipped")
            return BuildResult(
                status=BuildStatus.ERROR,
                stdout="",
                stderr="Maven (mvn) executable was not found on PATH",
                exit_code=-1,
                errors=["Maven (mvn) not installed; build validation cannot run"],
                warnings=["Build validation skipped because Maven is missing"],
                duration_seconds=0
            )
        except Exception as e:
            logger.error(f"Maven build error: {e}")
            return BuildResult(
                status=BuildStatus.ERROR,
                stdout="",
                stderr=str(e),
                exit_code=-1,
                errors=[str(e)],
                duration_seconds=0
            )
    
    def execute_tests(
        self,
        project_path: str,
        timeout: int = 600
    ) -> TestResult:
        """
        Execute Maven tests.
        
        Args:
            project_path: Path to pom.xml or module directory
            timeout: Test timeout in seconds
            
        Returns:
            TestResult with pass/fail counts
        """
        logger.info(f"Executing Maven tests: {project_path}")
        
        start_time = datetime.now()
        
        try:
            # Determine working directory
            if project_path.endswith("pom.xml"):
                work_dir = self.workspace_path / project_path.rsplit('/', 1)[0]
            else:
                work_dir = self.workspace_path / project_path
            
            # Run Maven test
            result = subprocess.run(
                ["mvn", "test"],
                cwd=work_dir,
                capture_output=True,
                text=True,
                timeout=timeout,
                shell=(sys.platform == "win32")
            )
            
            duration = (datetime.now() - start_time).total_seconds()
            
            # Parse test results
            test_counts = self._parse_maven_test_output(result.stdout)
            
            total = test_counts['total']
            passed = test_counts['passed']
            failed = test_counts['failed']
            skipped = test_counts['skipped']
            
            # Determine status
            if result.returncode == 0 and failed == 0:
                status = TestStatus.ALL_PASSED
            elif failed > 0 and passed > 0:
                status = TestStatus.SOME_FAILED
            elif passed == 0 and failed > 0:
                status = TestStatus.ALL_FAILED
            else:
                status = TestStatus.ERROR
            
            # Extract error messages
            errors = self._extract_test_failures(result.stdout)
            
            test_result = TestResult(
                status=status,
                total_tests=total,
                passed=passed,
                failed=failed,
                skipped=skipped,
                errors=errors,
                test_output=result.stdout,
                duration_seconds=duration
            )
            
            logger.info(
                f"Maven tests {status.value}: {passed}/{total} passed, "
                f"{failed} failed, {skipped} skipped"
            )
            return test_result
            
        except subprocess.TimeoutExpired:
            logger.error(f"Maven test timeout after {timeout}s")
            return TestResult(
                status=TestStatus.TIMEOUT,
                total_tests=0,
                passed=0,
                failed=0,
                errors=[f"Test timeout after {timeout} seconds"],
                test_output="",
                duration_seconds=timeout
            )
        except FileNotFoundError:
            logger.warning("Maven (mvn) not found on PATH — test execution skipped")
            return TestResult(
                status=TestStatus.ERROR,
                total_tests=0,
                passed=0,
                failed=0,
                errors=["Maven (mvn) not installed; test execution cannot run"],
                test_output="Maven (mvn) executable was not found on PATH",
                duration_seconds=0
            )
        except Exception as e:
            logger.error(f"Maven test error: {e}")
            return TestResult(
                status=TestStatus.ERROR,
                total_tests=0,
                passed=0,
                failed=0,
                errors=[str(e)],
                test_output="",
                duration_seconds=0
            )
    
    def _extract_maven_errors(self, output: str) -> list[str]:
        """Extract compilation errors from Maven output"""
        errors = []
        for line in output.split('\n'):
            line_str = line.strip()
            if not line_str or '[WARNING]' in line_str:
                continue
            if '[ERROR]' in line_str:
                # Match compilation failure header
                if 'compilation failure' in line_str.lower():
                    errors.append(line_str)
                # Match file location lines: e.g. [ERROR] /path/File.java:[123,45] ... or [ERROR] ...File.java:123: ...
                elif re.search(r'\.(?:java|kt|scala)(?::\[\d+,\d+\]|:\d+:|:\d+)', line_str, re.IGNORECASE):
                    errors.append(line_str)
                # Match compiler symbol / context lines: [ERROR] symbol: ... or [ERROR] location: ...
                elif re.search(r'\[ERROR\]\s+(?:symbol|location|cannot find|incompatible|package|class|method|expected)\b', line_str, re.IGNORECASE):
                    errors.append(line_str)
                elif re.search(r'\[\d+,\d+\]', line_str):
                    errors.append(line_str)
        # If no specific error lines matched, capture any [ERROR] line that is not a generic summary
        if not errors:
            for line in output.split('\n'):
                line_str = line.strip()
                if '[ERROR]' in line_str and not line_str.startswith('[ERROR] -> [Help 1]'):
                    errors.append(line_str)
        return errors[:30]  # Limit to first 30
    
    def _extract_maven_warnings(self, output: str) -> list[str]:
        """Extract warnings from Maven output"""
        warnings = []
        for line in output.split('\n'):
            if '[WARNING]' in line:
                warnings.append(line.strip())
        return warnings[:20]  # Limit to first 20
    
    def _parse_maven_test_output(self, output: str) -> dict:
        """
        Parse Maven test output for counts.
        
        Example:
        Tests run: 5, Failures: 0, Errors: 0, Skipped: 0
        """
        counts = {'total': 0, 'passed': 0, 'failed': 0, 'skipped': 0}
        
        # Look for summary line
        pattern = r'Tests run: (\d+), Failures: (\d+), Errors: (\d+), Skipped: (\d+)'
        matches = re.findall(pattern, output)
        
        if matches:
            # Take the last match (final summary)
            total, failures, errors, skipped = map(int, matches[-1])
            
            counts['total'] = total
            counts['failed'] = failures + errors  # Combine failures and errors
            counts['skipped'] = skipped
            counts['passed'] = total - counts['failed'] - counts['skipped']
        
        return counts
    
    def _extract_test_failures(self, output: str) -> list[str]:
        """Extract test failure messages"""
        errors = []
        in_failure = False
        
        for line in output.split('\n'):
            if 'Failed tests:' in line or 'Tests in error:' in line:
                in_failure = True
            elif in_failure:
                if line.strip() and not line.startswith('['):
                    errors.append(line.strip())
                if line.startswith('[INFO]') or line.startswith('[ERROR]'):
                    in_failure = False
        
        return errors[:10]  # Limit to first 10


class JavaGradleExecutor(ExecutionEngineBase):
    """
    Execution engine for Java projects using Gradle.
    
    Commands:
    - Build: gradle build
    - Test: gradle test
    - Clean: gradle clean
    """
    
    def get_technology_name(self) -> str:
        return "Java (Gradle)"
    
    def _get_project_file_patterns(self) -> list[str]:
        return ["build.gradle", "build.gradle.kts"]
    
    def execute_build(
        self, 
        project_path: str,
        timeout: int = 300
    ) -> BuildResult:
        """Execute Gradle build"""
        logger.info(f"Executing Gradle build: {project_path}")
        
        start_time = datetime.now()
        
        try:
            # Determine working directory
            if project_path.endswith("build.gradle") or project_path.endswith("build.gradle.kts"):
                work_dir = self.workspace_path / project_path.rsplit('/', 1)[0]
            else:
                work_dir = self.workspace_path / project_path
            
            # Determine Gradle executable
            gradle_exec = "gradle"
            if sys.platform == "win32":
                if (work_dir / "gradlew.bat").exists():
                    gradle_exec = str(work_dir / "gradlew.bat")
            else:
                if (work_dir / "gradlew").exists():
                    gradle_exec = str(work_dir / "gradlew")
            
            # Run Gradle build
            result = subprocess.run(
                [gradle_exec, "clean", "build", "-x", "test"],
                cwd=work_dir,
                capture_output=True,
                text=True,
                timeout=timeout,
                shell=(sys.platform == "win32")
            )
            
            duration = (datetime.now() - start_time).total_seconds()
            
            # Determine status
            if result.returncode == 0 and "BUILD SUCCESSFUL" in result.stdout:
                status = BuildStatus.SUCCESS
            else:
                status = BuildStatus.FAILURE
            
            # Extract errors
            errors = self._extract_gradle_errors(result.stdout + result.stderr)
            
            build_result = BuildResult(
                status=status,
                stdout=result.stdout,
                stderr=result.stderr,
                exit_code=result.returncode,
                errors=errors,
                duration_seconds=duration
            )
            
            logger.info(f"Gradle build {status.value}: {duration:.1f}s")
            return build_result
            
        except subprocess.TimeoutExpired:
            return BuildResult(
                status=BuildStatus.TIMEOUT,
                stdout="",
                stderr=f"Build timeout after {timeout} seconds",
                exit_code=-1,
                errors=[f"Build timeout after {timeout} seconds"],
                duration_seconds=timeout
            )
        except Exception as e:
            return BuildResult(
                status=BuildStatus.ERROR,
                stdout="",
                stderr=str(e),
                exit_code=-1,
                errors=[str(e)],
                duration_seconds=0
            )
    
    def execute_tests(
        self,
        project_path: str,
        timeout: int = 600
    ) -> TestResult:
        """Execute Gradle tests"""
        logger.info(f"Executing Gradle tests: {project_path}")
        
        start_time = datetime.now()
        
        try:
            if project_path.endswith("build.gradle") or project_path.endswith("build.gradle.kts"):
                work_dir = self.workspace_path / project_path.rsplit('/', 1)[0]
            else:
                work_dir = self.workspace_path / project_path
            
            # Determine Gradle executable
            gradle_exec = "gradle"
            if sys.platform == "win32":
                if (work_dir / "gradlew.bat").exists():
                    gradle_exec = str(work_dir / "gradlew.bat")
            else:
                if (work_dir / "gradlew").exists():
                    gradle_exec = str(work_dir / "gradlew")
            
            # Run Gradle test
            result = subprocess.run(
                [gradle_exec, "test"],
                cwd=work_dir,
                capture_output=True,
                text=True,
                timeout=timeout,
                shell=(sys.platform == "win32")
            )
            
            duration = (datetime.now() - start_time).total_seconds()
            
            # Parse test results
            test_counts = self._parse_gradle_test_output(result.stdout)
            
            total = test_counts['total']
            passed = test_counts['passed']
            failed = test_counts['failed']
            skipped = test_counts['skipped']
            
            # Determine status
            if result.returncode == 0 and failed == 0:
                status = TestStatus.ALL_PASSED
            elif failed > 0 and passed > 0:
                status = TestStatus.SOME_FAILED
            elif passed == 0 and failed > 0:
                status = TestStatus.ALL_FAILED
            else:
                status = TestStatus.ERROR
            
            test_result = TestResult(
                status=status,
                total_tests=total,
                passed=passed,
                failed=failed,
                skipped=skipped,
                test_output=result.stdout,
                duration_seconds=duration
            )
            
            logger.info(f"Gradle tests {status.value}: {passed}/{total} passed")
            return test_result
            
        except subprocess.TimeoutExpired:
            return TestResult(
                status=TestStatus.TIMEOUT,
                total_tests=0,
                passed=0,
                failed=0,
                errors=[f"Test timeout after {timeout} seconds"],
                test_output="",
                duration_seconds=timeout
            )
        except Exception as e:
            return TestResult(
                status=TestStatus.ERROR,
                total_tests=0,
                passed=0,
                failed=0,
                errors=[str(e)],
                test_output="",
                duration_seconds=0
            )
    
    def _extract_gradle_errors(self, output: str) -> list[str]:
        """Extract errors from Gradle output"""
        errors = []
        for line in output.split('\n'):
            if 'error:' in line.lower() or 'FAILED' in line:
                errors.append(line.strip())
        return errors[:20]
    
    def _parse_gradle_test_output(self, output: str) -> dict:
        """Parse Gradle test output"""
        counts = {'total': 0, 'passed': 0, 'failed': 0, 'skipped': 0}
        
        # Pattern: "5 tests completed, 0 failed, 0 skipped"
        pattern = r'(\d+) tests? completed, (\d+) failed, (\d+) skipped'
        match = re.search(pattern, output)
        
        if match:
            total = int(match.group(1))
            failed = int(match.group(2))
            skipped = int(match.group(3))
            
            counts['total'] = total
            counts['failed'] = failed
            counts['skipped'] = skipped
            counts['passed'] = total - failed - skipped
        
        return counts


# Register Java executors with factory
from ticket_to_code.execution.base_executor import ExecutionEngineFactory

ExecutionEngineFactory.register("java", JavaMavenExecutor)
ExecutionEngineFactory.register("java-maven", JavaMavenExecutor)
ExecutionEngineFactory.register("java-gradle", JavaGradleExecutor)

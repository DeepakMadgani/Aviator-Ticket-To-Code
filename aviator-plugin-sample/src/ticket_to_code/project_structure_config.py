"""
Project Structure Configuration

Defines how to map code artifacts to project structures for different codebases.

Author: Deepak Madgani
Date: April 2026
"""

import logging
from pathlib import Path
from typing import Dict, List, Optional
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger(__name__)


class ProjectStructureType(Enum):
    """Types of project structures"""
    CLEAN_ARCHITECTURE = "clean_architecture"  # src/Core, src/Infrastructure, src/Application
    FLAT_MULTI_PROJECT = "flat_multi_project"  # Each feature is separate project folder
    MODULAR_MONOLITH = "modular_monolith"      # Single project with feature folders
    MICROSERVICES = "microservices"            # Multiple independent services


@dataclass
class ProjectMapping:
    """Maps artifact types to project locations"""
    interfaces_path: str
    implementations_path: str
    services_path: str
    models_path: str
    utilities_path: str
    project_file_pattern: str  # e.g., "*.csproj", "pom.xml"


class ProjectStructureDetector:
    """
    Analyzes a codebase to determine its structure type and file organization.
    """
    
    @staticmethod
    def detect_structure(project_path: Path) -> tuple[ProjectStructureType, ProjectMapping]:
        """
        Auto-detect project structure type.
        
        Args:
            project_path: Root path of the project
            
        Returns:
            Tuple of (structure_type, project_mapping)
        """
        logger.info(f"Detecting project structure for: {project_path}")
        
        # Check for Clean Architecture
        if (project_path / "src" / "Core").exists() and \
           (project_path / "src" / "Infrastructure").exists():
            logger.info("Detected: Clean Architecture")
            return (
                ProjectStructureType.CLEAN_ARCHITECTURE,
                ProjectMapping(
                    interfaces_path="src/Core/Interfaces",
                    implementations_path="src/Infrastructure/Services",
                    services_path="src/Application/Services",
                    models_path="src/Core/Models",
                    utilities_path="src/Infrastructure/Utilities",
                    project_file_pattern="*.csproj"
                )
            )
        
        # Check for Flat Multi-Project (ContentBridge style)
        csproj_files = list(project_path.glob("*/*.csproj"))
        if len(csproj_files) > 10:  # Many separate project folders
            logger.info(f"Detected: Flat Multi-Project structure ({len(csproj_files)} projects)")
            return (
                ProjectStructureType.FLAT_MULTI_PROJECT,
                ProjectMapping(
                    interfaces_path="{project_folder}",
                    implementations_path="{project_folder}",
                    services_path="{project_folder}",
                    models_path="{project_folder}",
                    utilities_path="{project_folder}",
                    project_file_pattern="*.csproj"
                )
            )
        
        # Check for Modular Monolith
        if (project_path / "Modules").exists() or (project_path / "Features").exists():
            logger.info("Detected: Modular Monolith")
            return (
                ProjectStructureType.MODULAR_MONOLITH,
                ProjectMapping(
                    interfaces_path="Interfaces",
                    implementations_path="Services",
                    services_path="Services",
                    models_path="Models",
                    utilities_path="Utilities",
                    project_file_pattern="*.csproj"
                )
            )
        
        # Default to Clean Architecture assumption
        logger.warning("Could not detect structure, defaulting to Clean Architecture")
        return (
            ProjectStructureType.CLEAN_ARCHITECTURE,
            ProjectMapping(
                interfaces_path="src/Core/Interfaces",
                implementations_path="src/Infrastructure/Services",
                services_path="src/Application/Services",
                models_path="src/Core/Models",
                utilities_path="src/Infrastructure/Utilities",
                project_file_pattern="*.csproj"
            )
        )


class ContentBridgeStructureHelper:
    """
    Specific helper for ContentBridge's flat multi-project structure.
    """
    
    @staticmethod
    def find_appropriate_project(
        project_path: Path,
        artifact_type: str,
        feature_name: str
    ) -> Optional[str]:
        """
        Find the most appropriate project folder for a given artifact.
        
        Args:
            project_path: Root ContentBridge path
            artifact_type: Type of artifact (interface, service, utility, etc.)
            feature_name: Feature being implemented (e.g., "VersionLogging")
            
        Returns:
            Project folder name (e.g., "AddToDCTMQueue") or None
        """
        logger.info(f"Finding project for {artifact_type}: {feature_name}")
        
        # Search strategy for ContentBridge:
        # 1. Look for existing projects with related names
        # 2. Identify common/shared project for cross-cutting concerns
        # 3. Fall back to creating new project folder
        
        # Common ContentBridge project patterns
        project_keywords = {
            "logging": ["DataSlave.Core.Runtime", "MethodHelper"],
            "validation": ["AttributeValidation", "DataSlave.AddIn.Validate"],
            "data_access": ["DataSlave.Core.DataAccess"],
            "ui": ["DataSlave.Core.UI", "DataSlave.MapEditor"],
            "transform": ["DataSlave.AddIn.Transform"],
            "integration": ["LoadIntoDCTM3", "AddToDCTMQueue"],
            "utility": ["MethodHelper", "TrinityBridge.Helper"]
        }
        
        # Try to match feature to existing projects
        feature_lower = feature_name.lower()
        
        for category, projects in project_keywords.items():
            if category in feature_lower or any(kw in feature_lower for kw in ["log", "trace", "monitor"]):
                for project in projects:
                    if (project_path / project).exists():
                        logger.info(f"Matched to existing project: {project}")
                        return project
        
        # For cross-cutting concerns like logging, use MethodHelper (common utilities)
        if (project_path / "MethodHelper").exists():
            logger.info("Using MethodHelper for cross-cutting concern")
            return "MethodHelper"
        
        # For core runtime features, use DataSlave.Core.Runtime
        if (project_path / "DataSlave.Core.Runtime").exists():
            logger.info("Using DataSlave.Core.Runtime for core feature")
            return "DataSlave.Core.Runtime"
        
        logger.warning(f"No appropriate project found for {feature_name}")
        return None
    
    @staticmethod
    def get_file_path(
        project_name: str,
        file_name: str,
        subfolder: Optional[str] = None
    ) -> str:
        """
        Generate file path in ContentBridge project structure.
        
        Args:
            project_name: Project folder name (e.g., "MethodHelper")
            file_name: File name (e.g., "VersionLoggingService.cs")
            subfolder: Optional subfolder (e.g., "Interfaces", "Services")
            
        Returns:
            Relative file path (e.g., "MethodHelper/Services/VersionLoggingService.cs")
        """
        if subfolder:
            return f"{project_name}/{subfolder}/{file_name}"
        else:
            return f"{project_name}/{file_name}"


# Singleton instance for project structure info
_current_structure: Optional[tuple[ProjectStructureType, ProjectMapping]] = None


def get_project_structure(project_path: Path) -> tuple[ProjectStructureType, ProjectMapping]:
    """
    Get cached project structure information.
    
    Args:
        project_path: Root project path
        
    Returns:
        Tuple of (structure_type, project_mapping)
    """
    global _current_structure
    
    if _current_structure is None:
        _current_structure = ProjectStructureDetector.detect_structure(project_path)
    
    return _current_structure


def reset_project_structure():
    """Reset cached structure (useful for testing)"""
    global _current_structure
    _current_structure = None

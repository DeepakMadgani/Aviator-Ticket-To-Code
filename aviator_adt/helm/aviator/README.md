# Multi-Plugin InitContainer Support

## Overview

This Helm chart now supports multiple initContainers for loading Python plugins into the Aviator ADT deployment. This feature allows you to define multiple plugin Docker images that will be executed in sequence before the main application starts.

## Configuration

### Basic Structure

The configuration is defined in `values.yaml` under the `initContainers` section:

```yaml
initContainers:
  pluginVolume:
    name: plugin-volume            # Name of the shared volume
    mountPath: /plugins            # Path where plugins are mounted
    readOnly: true                 # ReadOnly for main container
  containers: []                   # Array of initContainer configurations
```

### Plugin Container Configuration

Each plugin container in the `containers` array supports:

```yaml
- name: plugin-init-name           # Unique name for the initContainer
  image:
    repository: registry.example.com   # Docker registry
    name: plugin-image-name        # Image name
    tag: latest                    # Image tag
    pullPolicy: Always             # Pull policy (Always, IfNotPresent, Never)
```

### Execution Order

InitContainers are executed in the order they appear in the `containers` array:
1. First initContainer completes successfully
2. Second initContainer starts and completes
3. ... and so on
4. Main application container starts

## Implementation Details

### Overview

1. **InitContainer Support**: Added conditional initContainer sections to both app and worker deployments
2. **Shared Volume**: EmptyDir volume shared between initContainers and main containers
4. **Resource Management**: Full support for resource requests/limits per plugin


### Volume Behavior

- **InitContainers**: Mount the plugin volume as read-write at `/plugins`
- **Main Container**: Mount the plugin volume as read-only at `/plugins`
- **Volume Type**: EmptyDir (ephemeral, shared between containers in the pod)


## Plugin Development Guidelines

### Plugin Structure

Each plugin Docker image should:
1. Copy plugin files to the shared volume mount point during init
2. Use a consistent directory structure in `/plugins`
3. Follow Python entry point conventions for plugin discovery

## Troubleshooting

1. **InitContainer Failures**: Check individual initContainer logs
2. **Plugin Not Found**: Verify `PLUGIN_PATH` environment variable and volume mounts
3. **Order Issues**: Ensure plugins are listed in correct dependency order

## Security Considerations

- Plugin volumes are ephemeral (EmptyDir)
- Main container has read-only access to plugin volume
- Consider using specific image tags instead of `latest` for production
- Resource limits prevent resource exhaustion from misbehaving plugins
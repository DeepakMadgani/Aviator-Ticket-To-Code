# Plugin Deployment with Init Containers in Aviator ADT

This document describes the complete process for building, configuring, and deploying plugins in Aviator ADT using init containers and shared volumes. It integrates best practices for Docker, Helm, and Kubernetes.

---

## Overview: Init Containers for Plugin Deployment

Init containers are specialized containers that run **before** the main application containers start. In Aviator ADT, init containers are used to copy plugin files (`.whl` or `.tar.gz`) to a shared volume.




The main Aviator container then reads plugins from the shared volume, initializes plugin dependencies and performs pre-startup configuration.


## Architecture (Current Plugin Image and Init Flow)

---
```
┌──────────────────────────────────────────────────────┐
│                         Pod                          |      
│  ┌──────────────────┐    ┌──────────────────┐        |     
│  │  Init Container │───▶ │  Main Container │         |   
│  │  (plugin-init)  │     │    (aviator)    │         |    
│  └────────┬─────────┘    └────────┬─────────┘        |   
│           │                       │                  |  
│           ▼                       ▼                  | 
│  ┌──────────────────────────────────────────┐        |
│  │          Shared Volume (/plugins)        │        |
│  │   - plugin-0.1.0.whl / plugin-0.1.0.tar.gz │       | 
│  │   - deps                                          │
│  └──────────────────────────────────────────┘        │
└──────────────────────────────────────────────────────┘
```

**Plugin Deployment Flow:**

    [Build Image]
        |
        |-- /dist/ (plugin .tar.gz, .whl)
        |-- /deps/ (dependency wheels)
        |-- entrypoint.sh
        |
    [Init Container]
        |
        |-- Runs entrypoint.sh
        |-- Copies from /dist to /plugins and /deps to /plugins/deps
        |
    [Main ADT Container]
        |
        |-- Mounts /plugins (read-only)
        |-- Loads plugins at startup

**How it works:**

- The plugin image is built with `/dist` (plugin artifacts), `/deps` (dependency wheels), and an `entrypoint.sh` script.
- The **init container** runs `entrypoint.sh`, which:
    - creates `/plugins`
    - copies plugin artifact from `/dist` → `/plugins`
    - copies dependency wheels from `/deps` → `/plugins/deps`
  If this exits with code 0, Kubernetes starts the main Aviator container.
- The **main Aviator container** reads `PLUGIN_PATH=/plugins`, mounts `/plugins` as read-only, and loads plugins from it at startup.

---

## 1. Dockerfile: Building the Plugin Image

### Multi-Stage Dockerfile Pattern for Aviator Plugin Deployment

This section explains the robust multi-stage Dockerfile pattern for building, packaging, and deploying Aviator plugins with external dependencies. It ensures reproducible builds, minimal runtime images, and compatibility with C-extension wheels.

#### Multi-Stage Dockerfile Overview

The Dockerfile uses three stages:
1. **Build Stage**: Builds the plugin and outputs distribution files.
2. **Deps Stage**: Downloads all external dependencies as wheels.
3. **Final Stage**: Assembles a minimal runtime image with plugin and dependencies.

**Build Stage**
- Uses a fast Python build image (uv:alpine).
- Copies project files to `/plugins`.
- Updates plugin version in `pyproject.toml` using build ARG.
- Runs `uv build` to create distribution files in `/plugins/dist`.

**Deps Stage**
- Uses `python:3.13-slim` for wheel compatibility.
- Optionally uses a custom PyPI index.
- Extracts non-aviator dependencies from `pyproject.toml`.
- Downloads all dependencies as wheels into `/deps`.

**Final Stage**
- Uses `busybox` for minimal runtime.
- Sets plugin version and commit SHA as labels.
- Copies plugin tarball and dependency wheels from previous stages.
- Prepares `/dist`, `/deps`, and `/entrypoint.sh` for runtime use.

**Benefits:**
- Reproducible builds and minimal runtime images
- C-extension compatibility
- Traceability with version and commit SHA labels

For full Dockerfile examples and troubleshooting, see the original multi-stage pattern documentation.

### Example Plugin Dockerfile

```dockerfile
# Build stage
FROM artifactory.otxlab.net/ghcr.io/astral-sh/uv:alpine as build
ADD . /plugins
WORKDIR /plugins
ARG VERSION=0.1.0

# Update the version in pyproject.toml
RUN sed -i "s/^version = \".*\"/version = \"${VERSION}\"/" pyproject.toml

# Build the plugin
RUN uv build --no-sources .


####################################################
# Deps stage - download external dependency packages as wheels.
# Uses a glibc-based image so that wheels with C extensions (e.g. lxml, pandas)
# are compatible with the UBI9 runtime image.
FROM artifactory.otxlab.net/dockerhub/python:3.13-slim as deps

# Configure package indexes. Override PIP_INDEX_URL for a PyPI mirror
ARG PIP_INDEX_URL="https://artifactory.otxlab.net/artifactory/api/pypi/pypi/simple"

COPY pyproject.toml /tmp/pyproject.toml

# Parse non-aviator dependencies from pyproject.toml and download them
# (including all transitive dependencies) as wheel files.
RUN mkdir -p /deps && \
    python3 -c "\
import tomllib, pathlib, sys; \
data = tomllib.loads(pathlib.Path('/tmp/pyproject.toml').read_text()); \
deps = [d for d in data['project']['dependencies'] if not d.lower().startswith('aviator')]; \
pathlib.Path('/tmp/ext-deps.txt').write_text(chr(10).join(deps)); \
print(f'External dependencies to download: {deps}')" && \
    pip download --dest /deps --requirement /tmp/ext-deps.txt


####################################################
# Final image
FROM artifactory.otxlab.net/dockerhub/busybox:latest as final
ARG VERSION=0.1.0
ARG COMMIT_SHA=$(git rev-parse --short HEAD)

LABEL PLUGIN_VERSION=${VERSION} \
      COMMIT_SHA=${COMMIT_SHA}

RUN mkdir -p /dist /deps
# Use either tar.gz or wheel depending on your preference. The ADT in the base image can handle both formats. Do not use both at the same time as it may cause issues with multiple versions of the same package.
COPY --from=build --chmod=0644 /plugins/dist/*.tar.gz /dist/
#COPY --from=build --chmod=0644 /plugins/dist/*.whl /dist/
COPY --from=deps --chmod=0644 /deps/ /deps/

RUN echo '#!/bin/sh' > /entrypoint.sh && \
    echo 'set -e' >> /entrypoint.sh && \
    echo 'echo "Installing plugin version ${PLUGIN_VERSION} (commit ${COMMIT_SHA})..."' >> /entrypoint.sh && \
    echo 'mkdir -p /plugins' >> /entrypoint.sh && \
    echo 'cp /dist/*.tar.gz /plugins/' >> /entrypoint.sh && \
  # echo 'cp /dist/*.whl /plugins/' >> /entrypoint.sh && \
    echo 'if ls /deps/* >/dev/null 2>&1; then' >> /entrypoint.sh && \
    echo '  mkdir -p /plugins/deps' >> /entrypoint.sh && \
    echo '  cp /deps/* /plugins/deps/' >> /entrypoint.sh && \
    echo 'fi' >> /entrypoint.sh && \
    chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
```

### Build and Verify the Plugin Image

```bash
# Build the plugin package
uv build

# Build the Docker image
docker build -t <your-image>:<tag> .


# Check the plugin files and structure in the image
docker run -it --rm --entrypoint /bin/sh <your-image>:<tag>
/ # sh entrypoint.sh

```

#### Example - Aviator Plugin Sample

```bash
# Build the plugin package
uv build

# Build the Docker image
docker build -t artifactory.otxlab.net/cs-csai-docker-dev/aviator-plugin-sample:latest .


# Check the plugin files and structure in the image
docker run -it --rm --entrypoint /bin/sh artifactory.otxlab.net/cs-csai-docker-dev/aviator-plugin-sample:latest
/ # sh entrypoint.sh

```

Expected output:
```

Installing plugin version  (commit )...
/ # ls
bin            dev            entrypoint.sh  home           lib64          proc           sys            usr
deps           dist           etc            lib            plugins        root           tmp            var


/dist:
total 24
drwxr-xr-x 2 root root 4096  .
drwxr-xr-x 1 root root 4096  ..
-rw-r--r-- 1 root root 12000 aviator_plugin_sample-0.1.0-py3-none-any.whl
-rw-r--r-- 1 root root  8000 aviator_plugin_sample-0.1.0.tar.gz

/deps:
total 16
drwxr-xr-x 2 root root 4096 Mar  2 20:42 .
drwxr-xr-x 1 root root 4096 Mar  4 22:05 ..
-rw-r--r-- 1 root root  4000 Mar  2 20:42 dependency1.whl
-rw-r--r-- 1 root root  4000 Mar  2 20:42 dependency2.whl

/entrypoint.sh:
-rwxr-xr-x 1 root root  512 Mar  2 20:42 /entrypoint.sh
```
---


## 2. Helm Chart: Declaring the Init Container
- In the Aviator ADT Helm chart (`aviator/templates/deployment-app.yaml`, `values.yaml`):
  - `initContainers.containers` array declares init containers.
  - Each init container can use your plugin image and runs before the main ADT container.

### Example Helm Configuration

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: {{ include "aviator.fullname" . }}
    spec:
      containers:
        - args:
            - app
          image: <aviator_adt_image>
          imagePullPolicy: Always
          volumeMounts:
            - mountPath: /plugins
              name: plugin-volume
              readOnly: true
      initContainers:
        - image: <your_plugin_image>
          imagePullPolicy: IfNotPresent
          name: plugin
          volumeMounts:
            - mountPath: /plugins
              name: plugin-volume
```

#### Configuration Reference

| Field | Description | Required |
|-------|-------------|----------|
| `pluginVolume.name` | Name of the shared volume | Yes |
| `pluginVolume.mountPath` | Mount path in main container | Yes |
| `pluginVolume.readOnly` | Whether main container mounts as read-only | No (default: true) |
| `containers[].name` | Unique name for the init container | Yes |
| `containers[].image.repository` | Image registry URL (e.g., `artifactory.otxlab.net/cs-csai-docker-dev`) | Yes (for production) |
| `containers[].image.name` | Image name | Yes |
| `containers[].image.tag` | Image tag | Yes |
| `containers[].image.pullPolicy` | Image pull policy (`Always` for production, `Never` for local) | No (default: Always) |
| `containers[].mountPath` | Mount path in init container | No (default: pluginVolume.mountPath) |
| `containers[].command` | Command to run | No |
| `containers[].args` | Arguments for command | No |


#### Multiple Init Containers

You can define multiple init containers that run in order:

```yaml
initContainers:
  pluginVolume:
    name: plugin-volume
    mountPath: /plugins
    readOnly: true
  containers:
    # First init container
    - name: plugin-init-core
      image:
        repository: <your_repository>
        name: aviator-plugin-core
        tag: 1.0.0
        pullPolicy: Always

    # Second init container (runs after first completes)
    - name: plugin-init-custom
      image:
        repository: <your_respository>
        name: aviator-plugin-custom
        tag: 2.0.0
        pullPolicy: Always
```

---

## 3. Kubernetes Pod: Shared Plugin Volume
- Shared volume (`plugin-volume`) is mounted at `/plugins` in both:
  - The init container (read-write)
  - The main ADT container (read-only)
- Init container runs first, populates `/plugins`.

---

## 4. Main Aviator ADT Container: Plugin Installation
- After init containers complete, the main ADT container starts.
- It mounts `/plugins` (now populated).
- The ADT base image detects and installs plugins from this directory at startup.

---

## 6. Deployment

### Production Deployment (Recommended)

For production environments, plugin images should be stored in and pulled from a remote container registry.

1. **Build the plugin image:**
   ```bash
   # Build the plugin package
   uv build
   # Build the Docker image
   docker build -t aviator-plugin-cs:0.1.0 ./path-to-plugin/
   ```
2. **Push the plugin image to registry:**
   ```bash
   # Tag for remote registry
   docker tag aviator-plugin-cs:0.1.0 <registry>/aviator-plugin-cs:0.1.0
   # Push to registry
   docker push <registry>/aviator-plugin-cs:0.1.0
   ```
3. **Configure Helm values** - add helm values particular to your environment environment-specific file 
   ```yaml
   initContainers:
     pluginVolume:
       name: plugin-volume
       mountPath: /plugins
       readOnly: true
     containers:
       - name: plugin_init_cs
         image:
           repository: <path_to_repository>
           name: aviator-plugin-cs
           tag: 0.1.0
           pullPolicy: Always

   ```
4. **Deploy with Skaffold:**
   ```bash
   skaffold run --profile <your-profile> --namespace <your-namespace>
   ```


### Local Development (Docker Desktop)

> **Note:** This approach is for local development and testing only. Do not use local images in production. This uses aviator-plugin-sample image and deploys it with Aviator ADT on Docker Desktop profile.

1. **Build and load the plugin image locally:**
   ```bash
   # Build from the plugin repo (sibling directory)
   # IMPORTANT: Use --target final for the lightweight init container image.
   # Without --target, Docker builds the last stage (runtime) which uses the ADT entrypoint and will fail.
   docker build --target final -t artifactory.otxlab.net/cs-csai-docker-dev/aviator-plugin-sample:latest ../aviator-plugin-sample
   ```
2. **Configure Helm values** in `helm/site-specific-values/docker-desktop.yaml`:
   ```yaml
   initContainers:
     containers:
       - name: aviator-plugin-sample
         image:
           repository: artifactory.otxlab.net/cs-csai-docker-dev
           name: aviator-plugin-sample
           tag: latest
           pullPolicy: IfNotPresent  # Use local image if available
   ```
   The image tag must match the tag used in `docker build`. With `pullPolicy: IfNotPresent`, Kubernetes uses the locally built image without pulling from the remote registry.

3. **Deploy with Skaffold:**
   ```bash
   NO_BUILD=1 skaffold run -p docker-desktop
   ```

---

## 7. Verification and Troubleshooting

```bash
# Check Pod Status
kubectl get pods -n <namespace>

# Check Init Container Logs
kubectl logs <pod-name> -n <namespace> -c <init-container-name>

# Check aviator container logs
kubectl logs aviator-5c448db48f-5gbb4 -n <namespace> -c plugin-init-sample

# Describe Pod for Init Container Status

kubectl describe pod <pod-name> -n <namespace>


#Look for the `Init Containers` section:

Init Containers:
  plugin-init-sample:
    Container ID:  docker://abc123...
    Image:         aviator-plugin-sample:0.1.0
    State:         Terminated
      Reason:      Completed
      Exit Code:   0


# Verify Plugins in Main Container
kubectl exec -it <pod-name> -n <namespace> -- ls -la /plugins

# Check Aviator Logs for Plugin Loading
kubectl logs <pod-name> -n <namespace> | grep -i plugin

# Run deployed application
Port forward	 ->  kubectl port-forward -n <namespace> svc/chat-svc 3000:80
```

#### Troubleshooting

- **Init Container Stuck in ContainerCreating**
  - *Cause:* Image cannot be pulled or volume mount issues.
  - *Solution:* `kubectl describe pod <pod-name> -n <namespace>` and check Events.
- **Volume Mount Issues**
  - *Cause:* The volume overwrites the `/plugins` directory in the init container.
  - *Solution:* Mount the shared volume at a different path in the init container (e.g., `/shared-plugins`) and use `cp -r /plugins/* /shared-plugins/`.
- **Plugin Not Loaded by Aviator**
  - *Cause:* Plugin files not copied correctly or PLUGIN_PATH not set.
  - *Solution:*
    1. Verify files in the shared volume: `kubectl exec -it <pod-name> -n <namespace> -- ls -la /plugins`
    2. Check PLUGIN_PATH env: `kubectl exec -it <pod-name> -n <namespace> -- env | grep PLUGIN`
    3. Check Aviator startup logs for plugin loading errors.

---

## 8. Environment Variables

The Helm chart automatically sets the `PLUGIN_PATH` environment variable when init containers are configured:

```yaml
env:
  - name: PLUGIN_PATH
    value: "/plugins"
```

Aviator uses this path to discover and install plugins at startup.

---

## 9. References and Further Reading
 - **Dockerfile:** See final stage and `entrypoint.sh` in `aviator-plugin-sample/Dockerfile`.
- **Helm Example:** See `values.yaml` in `aviator_adt/helm/aviator/`.
- **Site-Specific Values:** See `docker-desktop.yaml` in `aviator_adt/helm/site-specific-values/`.
- [Plugins Development Guide](plugins.md)
- [Helm Deployment Guide](../helm.md) 
- [Kubernetes Init Containers Documentation](https://kubernetes.io/docs/concepts/workloads/pods/init-containers/)
- [Helm Templates Documentation](https://helm.sh/docs/chart_template_guide/)

---


## Best Practices
- Keep init containers lightweight and focused on a single responsibility.
- Use official images (e.g., busybox, alpine) for simple tasks.
- Ensure init containers have the necessary permissions and environment variables.
- Monitor init container logs for troubleshooting startup issues.



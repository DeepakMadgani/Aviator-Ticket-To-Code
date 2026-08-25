# How to Check Available Gemini Models via GCP Service Account

> [!CAUTION]
> **SECURITY WARNING:** Never expose your live GCP Service Account Private Key (the `.json` file containing `private_key`, `private_key_id`, and `client_id`) in public prompts, logs, or repositories. Anyone with access to this file can authenticate as your service account and access your Google Cloud resources.
>
> **If a key is exposed:** Go to the Google Cloud Console → IAM & Admin → Service Accounts, select your service account, navigate to the Keys tab, delete/revoke the exposed key immediately, and generate a new one.

## Understanding the JSON Credential File
The `otl-cs-csai.json` file is a **Google Cloud Platform (GCP) Service Account key file**. 

It provides authentication and permission credentials for your project (`otl-cs-csai`) so your application can securely call Google Cloud APIs (such as Vertex AI). It does not lock you into a single Gemini model; instead, it gives your code access to **any Gemini model enabled in your GCP project**.

---

## Step-by-Step Guide: Listing Available Models

Here is exactly how you can query Google Cloud to list all the Gemini models your service account has access to, using the Python SDK.

### Step 1: Set the Credentials Environment Variable
First, tell your terminal where to find your service account JSON file. In Windows Command Prompt, use `set`:

```cmd
set GOOGLE_APPLICATION_CREDENTIALS=C:\Users\dmadgani\Desktop\My_Aviator\aviator-plugin-sample\otl-cs-csai.json
```
*(You can verify it worked by running `echo %GOOGLE_APPLICATION_CREDENTIALS%`)*

### Step 2: Install the SDK
Ensure your pip is up to date and install the official Google GenAI SDK:

```cmd
python.exe -m pip install --upgrade pip
pip install google-genai
```

### Step 3: Create the Python Script
Create a file named `list_models.py` and add the following code. This script automatically uses the credentials you set in Step 1 to authenticate with the Vertex AI endpoint.

```python
from google import genai

PROJECT_ID = "otl-cs-csai"
LOCATION = "us-central1"

def list_available_models():
    try:
        client = genai.Client(vertexai=True, project=PROJECT_ID, location=LOCATION)
        
        print("Fetching available models...\n")
        for model in client.models.list():
            if "gemini" in model.name.lower():
                print(f"- {model.name}")
                
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    list_available_models()
```

### Step 4: Run the Script
Execute the script from your terminal:

```cmd
python list_models.py
```

### Example Output
When run successfully, it will output the real-time list of foundational models available to your project, which looks like this:

```text
Fetching available models...

- publishers/google/models/gemini-embedding-001
- publishers/google/models/gemini-2.5-pro
- publishers/google/models/gemini-2.5-flash
- publishers/google/models/gemini-2.5-flash-lite
- publishers/google/models/gemini-2.5-computer-use-preview-10-2025
- publishers/google/models/gemini-2.5-flash-image
- publishers/google/models/gemini-2.5-pro-tts
- publishers/google/models/gemini-2.5-flash-tts
- publishers/google/models/gemini-3-pro-preview
- publishers/google/models/gemini-live-2.5-flash-native-audio
- publishers/google/models/gemini-3-flash-preview
- publishers/google/models/gemini-3.1-flash-lite-preview
- publishers/google/models/gemini-3.1-flash-image-preview
- publishers/google/models/gemini-3.1-pro-preview
- publishers/google/models/gemini-3.5-flash
- publishers/google/models/gemini-3.1-flash-tts-preview
- publishers/google/models/gemini-3.1-flash-image
- publishers/google/models/gemini-3-pro-image
- publishers/google/models/gemini-omni-flash-preview
- publishers/google/models/gemini-embedding-2
- publishers/google/models/gemini-3.1-flash-lite
- publishers/google/models/gemini-3.1-flash-lite-image
- publishers/google/models/gemini-3.5-flash-lite
- publishers/google/models/gemini-3.6-flash
- publishers/google/models/gemini-robotics-er-2-preview-info
- publishers/google/models/gemini-3.7-flash
```

---

## How to Specify a Model in Code
When using this service account in your actual application, you specify the exact model name from the list above directly in your API calls:

```python
from google import genai

client = genai.Client(vertexai=True, project="otl-cs-csai", location="us-central1")

# Pass the desired model name directly (e.g., gemini-2.5-pro)
response = client.models.generate_content(
    model="gemini-2.5-pro",
    contents="Hello, world!",
)

print(response.text)
```

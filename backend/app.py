import os
from fastapi import FastAPI
from dotenv import load_dotenv
from fastapi.middleware.cors import CORSMiddleware


app = FastAPI()
ENV_PATH = os.path.join(os.path.dirname(__file__), ".env")
load_dotenv(dotenv_path=ENV_PATH, override=True)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Qualtrics/LLM Connection
_datacenter = os.getenv("DATACENTER_ID")
BASE_URL = f"https://{_datacenter}.qualtrics.com/API/v3"
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY") or os.getenv("API_KEY")


# Sign in with Google
SCOPES = ['https://www.googleapis.com/auth/forms.body.readonly']
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET")
VALID_EMAILS = [
    "rvjain@wisc.edu",
    "cho275@wisc.edu",
    "ekim298@wisc.edu",
    "mli936@wisc.edu",
    "hliu787@wisc.edu",
    "rpshah3@wisc.edu",
    "ksong65@wisc.edu",
    "jtong9@wisc.edu",
    "mzeng27@wisc.edu",
]

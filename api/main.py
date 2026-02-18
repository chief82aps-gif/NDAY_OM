from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from api.src.routes import uploads

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(uploads.router, prefix="/upload")

@app.get("/")
def root():
    return {"message": "NDAY_OM API is running."}

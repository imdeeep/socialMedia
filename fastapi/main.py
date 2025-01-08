from typing import Union
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, validator
import json
import requests
from typing import Optional, Dict, Any
from scrap import scrape_instagram_profile
import logging

app = FastAPI()

# Constants
BASE_API_URL = "https://api.langflow.astra.datastax.com"
LANGFLOW_ID = "9edb9b8e-f9ed-418a-8cf9-4c44fb6c36b7"
FLOW_ID = "8f31b8ec-f233-42de-80b2-e4531bdd709e"
APPLICATION_TOKEN = "AstraCS:hjwrCuNlkpqahuXnwaxfbkov:c7a436b1c173057053ed972f3d1e0bca2743e9078473307d9a5c8956f5976aa4"
ENDPOINT = FLOW_ID

# Default tweaks
TWEAKS = {
    "ChatInput-PVxoG": {},
    "ChatOutput-QqjiD": {},
    "Prompt-pXVnT": {},
    "Agent-UcVdC": {
        "temperature": 0.7,
        "model_name": "gpt-3.5-turbo"  # or whatever model you're using
    },
    "AstraDBToolComponent-Wy97B": {
        "collection_name": "instagram_data",
        "database_name": "your_database_name",  # replace with your actual database name
        "keyspace_name": "your_keyspace"        # replace with your actual keyspace
    }
}

# Request model
class FlowRequest(BaseModel):
    message: str
    tweaks: Optional[Dict[str, Any]] = TWEAKS
    output_type: str = "chat"
    input_type: str = "chat"

# Add logging configuration
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def run_flow(message: str,
                  endpoint: str,
                  output_type: str = "chat",
                  input_type: str = "chat",
                  tweaks: Optional[dict] = None) -> dict:
    """Run a flow with a given message and optional tweaks."""
    api_url = f"{BASE_API_URL}/lf/{LANGFLOW_ID}/api/v1/run/{endpoint}"

    payload = {
        "input_value": message,
        "output_type": output_type,
        "input_type": input_type,
    }
    
    headers = {
        "Authorization": f"Bearer {APPLICATION_TOKEN}",
        "Content-Type": "application/json"
    }

    if tweaks:
        payload["tweaks"] = tweaks

    try:
        response = requests.post(api_url, json=payload, headers=headers)
        response.raise_for_status()
        return response.json()
    except requests.RequestException as e:
        logger.error(f"Request failed: {str(e)}")
        if hasattr(e.response, 'text'):
            logger.error(f"Response text: {e.response.text}")
        raise HTTPException(
            status_code=500, 
            detail=f"Flow execution failed: {str(e)}"
        )

def clean_response(raw_response):
    """Clean and format the Langflow response"""
    try:
        logger.info(f"Raw response: {json.dumps(raw_response, indent=2)}")
        
        if not raw_response.get('outputs'):
            return {
                "status": "error",
                "message": "No outputs in response"
            }

        outputs = raw_response['outputs'][0]
        
        # Handle case where outputs might be empty or malformed
        if not outputs or not isinstance(outputs, dict):
            return {
                "status": "error",
                "message": "Malformed output structure"
            }

        # Extract message from nested structure
        message = (outputs.get('outputs', [{}])[0]
                  .get('results', {})
                  .get('message', {}))

        cleaned_response = {
            "status": "success",
            "message": {
                "text": message.get('text', ''),
                "timestamp": message.get('timestamp', ''),
                "session_id": message.get('session_id', '')
            }
        }

        # Extract content blocks if they exist
        content_blocks = message.get('content_blocks', [])
        if content_blocks:
            cleaned_response['agent_steps'] = [
                block.get('contents', [])
                for block in content_blocks
                if block.get('title') == 'Agent Steps'
            ]

        return cleaned_response

    except Exception as e:
        logger.error(f"Error formatting response: {str(e)}")
        return {
            "status": "error",
            "message": f"Error formatting response: {str(e)}"
        }

@app.get("/")
def read_root():
    return {"Applicaiton Working": "True"}

@app.post("/run-flow")
async def process_flow(request: FlowRequest):
    """
    Process a flow with the given message and parameters
    """
    try:
        logger.info(f"Processing request with message: {request.message}")
        
        raw_response = await run_flow(
            message=request.message,
            endpoint=ENDPOINT,
            output_type=request.output_type,
            input_type=request.input_type,
            tweaks=request.tweaks
        )
        
        # Format the response
        formatted_response = clean_response(raw_response)
        
        if formatted_response.get("status") == "error":
            raise HTTPException(
                status_code=500,
                detail=formatted_response.get("message", "Unknown error occurred")
            )
            
        return formatted_response
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected error: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"An unexpected error occurred: {str(e)}"
        )

# Health check endpoint
@app.get("/health")
async def health_check():
    return {"status": "healthy"}  #124 lines

class InstagramRequest(BaseModel):
    username: str
    results_limit: int = 5

    @validator('results_limit')
    def validate_results_limit(cls, v):
        if v < 1:
            raise ValueError('results_limit must be at least 1')
        if v > 50:  # Set a reasonable maximum
            raise ValueError('results_limit cannot exceed 50')
        return v

    class Config:
        json_schema_extra = {
            "example": {
                "username": "cristiano",
                "results_limit": 20
            }
        }

class PostInsertStatus(BaseModel):
    post_id: str
    post_data: dict
    post_number: str
    db_insert_status: bool
    insert_message: str

class InstagramResponse(BaseModel):
    success: bool
    profile_data: Optional[dict]
    posts_data: list[PostInsertStatus]
    total_posts: int
    error: Optional[str] = None
    db_status: bool
    db_message: str

@app.post("/scrape-instagram/", response_model=InstagramResponse)
async def scrape_instagram(request: InstagramRequest):
    """
    Scrape Instagram profile and posts data with database insertion status
    
    Parameters:
    - username: Instagram username to scrape
    - results_limit: Number of posts to fetch (default: 5)
    
    Returns:
    - Profile data
    - Posts data with individual DB insertion status
    - Total posts count
    - Database operation status
    """
    try:
        result = scrape_instagram_profile(
            username=request.username,
            results_limit=request.results_limit
        )
        
        if not result['success']:
            raise HTTPException(
                status_code=400,
                detail=result['error'] or "Failed to scrape Instagram data"
            )
        
        # Add total_posts to the result
        result['total_posts'] = result['profile_data'].get('total_posts', len(result['posts_data']))
            
        return {
            "success": result['success'],
            "profile_data": result['profile_data'],
            "posts_data": result['posts_data'],
            "total_posts": result['total_posts'],
            "error": result.get('error'),
            "db_status": result.get('db_status', False),
            "db_message": result.get('db_message', '')
        }
        
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=str(e)
        )

@app.get("/test-config")
async def test_configuration():
    """Test the configuration and connections"""
    try:
        # Test basic message
        test_response = await run_flow(
            message="Hello, this is a test message",
            endpoint=ENDPOINT,
            tweaks=TWEAKS
        )
        return {
            "status": "success",
            "config_test": "passed",
            "langflow_id": LANGFLOW_ID,
            "flow_id": FLOW_ID,
            "test_response": test_response
        }
    except Exception as e:
        logger.error(f"Configuration test failed: {str(e)}")
        return {
            "status": "error",
            "config_test": "failed",
            "error_message": str(e),
            "langflow_id": LANGFLOW_ID,
            "flow_id": FLOW_ID
        }


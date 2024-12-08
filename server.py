import os
import base64
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
import asyncio
from dotenv import load_dotenv
from assistant import VoiceSQLAssistant

# Load environment variables
load_dotenv()

app = FastAPI()

# Enable CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Get the current file's directory
current_dir = os.path.dirname(os.path.abspath(__file__))
# Construct path to frontend directory
frontend_dir = os.path.join(os.path.dirname(current_dir), 'frontend')

# Verify frontend directory exists
if not os.path.exists(frontend_dir):
    os.makedirs(frontend_dir)
    print(f"Created frontend directory at: {frontend_dir}")

# Initialize the voice assistant
dg_api_key = os.getenv("DEEPGRAM_API_KEY")
openai_api_key = os.getenv("OPENAI_API_KEY")

assistant = VoiceSQLAssistant(dg_api_key, openai_api_key)

# WebSocket endpoint
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    session_active = True
    

    async def listen_once():
        try:
            transcript = await assistant.listen_for_speech()
            if not transcript:
               await websocket.send_json({"type": "no_speech_detected"})
               return None
            
            await websocket.send_json({
                "type": "transcript",
                "text": transcript
            })
            
            if any(phrase in transcript.lower() for phrase in 
                  ['stop', 'quit', 'exit', 'bye', 'goodbye']):
                return "terminate"
            
            response = await assistant.process_query(transcript)
            if response:
                audio_data = await assistant.get_speech_audio(response)
                await websocket.send_json({
                    "type": "assistant_response",
                    "text": response,
                    "audioData": audio_data
                })
            return response
            
        except Exception as e:
            print(f"Error in listen_once: {str(e)}")
            return None

    try:
        await websocket.send_json({
            "type": "available_databases",
            "databases": assistant.available_databases
        })
        
        while session_active:
            data = await websocket.receive_json()
            
            if data["type"] == "start_listening":
                result = await listen_once()
                if result == "terminate":
                    goodbye_text = "Goodbye! Have a great day!"
                    audio_data = await assistant.get_speech_audio(goodbye_text)
                    await websocket.send_json({
                        "type": "session_ended",
                        "message": goodbye_text,
                        "audioData": audio_data
                    })
                    session_active = False
            
            elif data["type"] == "select_database":
                success = await assistant.handle_database_switch(data.get("database"))
                if success:
                    welcome_text = f"Connected to {assistant.selected_db_name} database. How can I help you?"
                    audio_data = await assistant.get_speech_audio(welcome_text)
                    await websocket.send_json({
                        "type": "database_selection",
                        "success": True,
                        "selected": assistant.selected_db_name,
                        "text": welcome_text,
                        "audioData": audio_data
                    })
                else:
                    await websocket.send_json({
                        "type": "database_selection",
                        "success": False,
                        "message": "Failed to connect to database"
                    })
            
            elif data["type"] == "end_session":
                session_active = False
                
    except WebSocketDisconnect:
        print("WebSocket disconnected")
    except Exception as e:
        print(f"WebSocket error: {str(e)}")
    finally:
        await websocket.close()

# Mount static files after WebSocket endpoint definition
app.mount("/", StaticFiles(directory=frontend_dir, html=True), name="static")

@app.get("/health")
async def health_check():
    return {"status": "healthy"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="localhost", port=8000)
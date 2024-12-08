import asyncio
import os
import tempfile
import time
import wave
import numpy as np
import base64
from sqlalchemy import create_engine, text
from urllib.parse import quote_plus
from dotenv import load_dotenv
from deepgram import DeepgramClient, PrerecordedOptions, FileSource, SpeakOptions
import pyaudio
from langchain_community.utilities.sql_database import SQLDatabase
from langchain_openai import ChatOpenAI
from langchain_community.agent_toolkits.sql.toolkit import SQLDatabaseToolkit
from langchain import hub
from langchain.memory import ConversationEntityMemory, ConversationSummaryBufferMemory
from langgraph.prebuilt import create_react_agent
from mode_tracker import ModeTracker, ConversationMode
class VoiceSQLAssistant:
    def __init__(self, dg_api_key: str, openai_api_key: str):
        self.dg_client = DeepgramClient(dg_api_key)
        self.setup_sql_agent(openai_api_key)
        self.mode_tracker = ModeTracker()
        self.available_databases = []
        self.refresh_available_databases()
        self.current_connection = None
        
        # Initialize specialized memories
        self.entity_memory = ConversationEntityMemory(
            llm=self.llm,
            max_token_limit=1000, 
            input_key="input", 
            output_key="output", 
            return_messages=True
        )
        
        self.conversation_memory = ConversationSummaryBufferMemory(
            llm=self.llm,
            max_token_limit=1000,
            input_key="input", 
            output_key="output",
            return_messages=True
        )

    async def get_speech_audio(self, text: str) -> dict:
        """Generate speech audio and return as base64 data"""
        if not text:
            return None
                
        try:
            formatted_text = ' '.join(
                word.title() if word.isupper() and len(word) > 1 
                else word 
                for word in text.split()
            )
            
            options = SpeakOptions(model="aura-asteria-en")
            
            with tempfile.NamedTemporaryFile(delete=False, suffix='.mp3') as temp_file:
                temp_filename = temp_file.name
            
            # Generate audio file
            self.dg_client.speak.v("1").save(temp_filename, {"text": formatted_text}, options)
            
            # Read file as bytes and encode as base64
            with open(temp_filename, 'rb') as audio_file:
                audio_bytes = audio_file.read()
                audio_base64 = base64.b64encode(audio_bytes).decode('utf-8')
            
            # Clean up temp file
            os.unlink(temp_filename)
            
            return {
                "audio": audio_base64,
                "type": "audio/mp3"
            }
            
        except Exception as e:
            print(f"Text-to-speech error: {str(e)}")
            return None

    def setup_sql_agent(self, openai_api_key):
        username = os.getenv("DB_USER", "harsha")
        password = quote_plus(os.getenv("DB_PASSWORD", "HarshaV@123"))
        host = os.getenv("DB_HOST", "localhost")
        connection_string = f"mysql+pymysql://{username}:{password}@{host}:3306"
        # Test connection
        self.engine = create_engine(connection_string, pool_pre_ping=True, pool_recycle=3600)
        with self.engine.connect() as conn:
            conn.execute(text("SELECT 1"))
            print("Database connection successful")
        self.llm = ChatOpenAI(
            model="gpt-3.5-turbo", 
            api_key=openai_api_key, 
            temperature=0,
            presence_penalty=0.6,  # Discourage repetition
            frequency_penalty=0.6,  # Encourage conciseness
            
        )
        self.db = None
        self.agent_executor = None

    def refresh_available_databases(self):
        try:
            with self.engine.connect() as conn:
                result = conn.execute(text("SHOW DATABASES"))
                self.available_databases = [
                    row[0] for row in result 
                    if row[0] not in ['information_schema', 'mysql', 'performance_schema', 'sys']
                ]
        except Exception as e:
            print(f"Error refreshing databases: {str(e)}")

    async def listen_for_speech(self, timeout_seconds: int = 15) -> str:
        CHUNK = 1024
        FORMAT = pyaudio.paInt16
        CHANNELS = 1
        RATE = 16000
        SILENCE_THRESHOLD = 1000
        MIN_AUDIO_LENGTH = 3
        
        p = pyaudio.PyAudio()
        stream = p.open(format=FORMAT, channels=CHANNELS, rate=RATE, input=True, frames_per_buffer=CHUNK)
        
        print("\n🎤 Listening for speech...")
        frames = []
        start_time = time.time()
        has_speech = False
        silence_frames = 0
        
        while True:
            try:
                data = stream.read(CHUNK, exception_on_overflow=False)
                frames.append(data)
                audio_data = np.frombuffer(data, dtype=np.int16)
                amplitude = np.max(np.abs(audio_data))
                
                if amplitude > SILENCE_THRESHOLD:
                    has_speech = True
                    silence_frames = 0
                elif has_speech:
                    silence_frames += 1
                    if silence_frames > 20:
                        break
                        
                if time.time() - start_time > timeout_seconds:
                    break
                    
            except Exception as e:
                print(f"Error reading audio stream: {str(e)}")
                break

        print("✓ Processing speech...")
        stream.stop_stream()
        stream.close()
        p.terminate()
        
        if not has_speech or len(frames) < int(RATE * MIN_AUDIO_LENGTH / CHUNK):
            return ""
                
        with tempfile.NamedTemporaryFile(delete=False, suffix='.wav') as temp_file:
            temp_filename = temp_file.name
            
        with wave.open(temp_filename, 'wb') as wf:
            wf.setnchannels(CHANNELS)
            wf.setsampwidth(p.get_sample_size(FORMAT))
            wf.setframerate(RATE)
            wf.writeframes(b''.join(frames))
            
        with open(temp_filename, 'rb') as audio:
            buffer_data = audio.read()
                
        payload = {"buffer": buffer_data}
        options = PrerecordedOptions(
            model="nova-2",
            smart_format=True,
            language="en-US",
            punctuate=True
        )
        
        try:
            response = self.dg_client.listen.rest.v("1").transcribe_file(payload, options)
            os.unlink(temp_filename)
            
            if hasattr(response.results, 'channels'):
                transcript = response.results.channels[0].alternatives[0].transcript
                print(f"\n✓ Transcribed: {transcript}")
                return transcript
        except Exception as e:
            print(f"Transcription error: {str(e)}")
            os.unlink(temp_filename)
            return ""
        
        return ""

    async def process_query(self, query: str):
        if not query:
            return None

        try:
            
            First_prompt = f"""
            Analyze this query exactly: "{query}"
            
            Classification Rules:

            1. QUERY - Database information requests:
            - Direct SQL queries
            - Table data requests
            - Record searches
            - Database statistics
            Examples: 
            "Show me sales data"
            "How many customers do we have"
            "What are the top movies"
            "Get rental information"

            2. SWITCH - Database selection/switching:
            - Explicit requests to change database
            - Database connection requests
            Examples:
            "Switch to movierental database"
            "Use the sales database"
            "Connect to northwind"
            "Change to customer database"

            3. LIST - Database listing:
            - Requests to show available databases
            - Database enumeration
            Examples:
            "Show me all databases"
            "What databases are available"
            "List the databases"
            "Show database options"

            4. CREATIVE - Content generation:
            - Poetry requests
            - Story writing
            - Creative descriptions
            - Artistic content
            Examples:
            "Write a poem about spring"
            "Tell me a story"
            "Create a description of sunset"
            "Make up a character"

            5. EXPLANATION - Understanding requests:
            - How things work
            - Why things happen
            - Process clarification
            - Concept explanations
            Examples:
            "Explain how this works"
            "Why did that happen"
            "How does this system function"
            "What's the reason for this"

            6. GENERAL - Default interactions:
            - Casual conversation
            - Simple questions
            - Basic interactions
            - Non-specific queries
            Examples:
            "How are you"
            "What's new"
            "Nice to meet you"
            "That's interesting"

            7. TRANSITIONING - Context changes:
            - Topic switches
            - Subject changes
            - Context shifts
            Examples:
            "Let's talk about something else"
            "Moving on to"
            "Can we discuss"
            "Switching topics to"

            Output exactly one word: QUERY, SWITCH, LIST, CREATIVE, EXPLANATION, GENERAL, or TRANSITIONING
            For this specific query, the classification is:
            """
            
            query_type = await self.get_llm_response(First_prompt)
            query_type = query_type.strip().upper()
            print(f"\nQuery Classification: {query_type}")
            mode_mapping = {
            "DATABASE": ConversationMode.DATABASE,
            "CREATIVE": ConversationMode.CREATIVE,
            "EXPLANATION": ConversationMode.EXPLANATION,
            "GENERAL": ConversationMode.GENERAL,
            "QUERY": ConversationMode.DATABASE,
            "TRANSITIONING": ConversationMode.TRANSITIONING,
            "SWITCH": ConversationMode.DATABASE,
            "LIST": ConversationMode.DATABASE
            }
            
            mode = mode_mapping.get(query_type, ConversationMode.GENERAL)
            mode_changed = self.mode_tracker.update_mode(mode, 0.9)
            if mode_changed:
                print(f"Mode switched to: {mode.value}")
                print(f"Previous mode was: {self.mode_tracker.previous_mode.value if self.mode_tracker.previous_mode else 'None'}")
            # Load both general and specialized memory contexts
            memory_context = self.conversation_memory.load_memory_variables({"input": query})
            if query_type == "GENERAL":
                return await self.handle_general_conversation(query)
            elif query_type == "SWITCH":
                switch_result = await self.handle_database_switch(query)
                if switch_result:
                    return f"Switched to {self.selected_db_name} database. What would you like to know?"
            elif mode == ConversationMode.CREATIVE:
                return await self.handle_creative_request(query)

            elif mode == ConversationMode.EXPLANATION:
                return await self.handle_explanation_request(query)

            elif mode == ConversationMode.TRANSITIONING:
                return await self.handle_transition(query)        
            elif query_type == "LIST":
                db_list = ', '.join(self.available_databases)
                return f"Available databases are: {db_list}. Which one would you like to explore?"
            elif query_type == "QUERY":
                if not self.agent_executor:
                    return "Please select a database first before making database queries."
                else:
                    # Include both general and DB-specific context
                    entity_context = self.entity_memory.load_memory_variables({"input": query})
                    enhanced_query = f"""
                    Previous conversation context: {memory_context.get('history', '')}
                    Entity Memory: {entity_context}
                    
                    Current Query: {query}
                    """
                    
                    events = self.agent_executor.stream(
                        {"messages": [("user", enhanced_query)]},
                        stream_mode="values",
                    )
                    
                    response = None
                    for event in events:
                        message = event["messages"][-1]
                        if (
                            hasattr(message, 'content') 
                            and not message.content.startswith('Tool Calls:')
                            and not message.content.startswith('Name: ')
                        ):
                            response = message.content
                    
                    if response:
                        # Save context for database queries
                        self.entity_memory.save_context(
                            {"input": query, "entities": {"db": self.db.schema if hasattr(self.db, 'schema') else None}},
                            {"output": response}
                        )
            
            # Save all interactions to the general conversation memory
            if response:
                self.conversation_memory.save_context(
                    {"input": query},
                    {"output": response}
                )
                
            return response
            
        except Exception as e:
            print(f"Error processing query: {str(e)}")
            return "I encountered an error processing your query. Please try rephrasing your question."
    async def get_llm_response(self, prompt: str) -> str:
        try:
            response = await asyncio.wait_for(
                asyncio.to_thread(self.llm.invoke, prompt),
                timeout=30
            )
            return response.content if response else ""
        except asyncio.TimeoutError:
            print("LLM request timed out")
            return "I'm taking too long to process that. Could you please try again?"
        except asyncio.CancelledError:
            print("\nOperation cancelled by user")
            return "Operation cancelled"
        except Exception as e:
            print(f"LLM error: {str(e)}")
            return "I encountered an error. Could you please try again?"

    async def handle_database_switch(self, query_or_db_name: str) -> bool:
        """
        Handle switching between databases with comprehensive error handling and validation.
        
        Args:
            query_or_db_name (str): Either the database name or a query containing the database name
            
        Returns:
            bool: True if switch successful, False otherwise
        """
        try:
            # Extract database name if passed in query form
            db_matching_prompt = f"""
            User query: "{query_or_db_name}"
            Available databases: {self.available_databases}
            Task: Extract exact database name from query.
            Return NONE if no match found.
            Output one database name or NONE:"""
            
            selected_db = await self.get_llm_response(db_matching_prompt)
            selected_db = selected_db.strip().lower()
            
            # Normalize database names for matching
            available_dbs = {db.lower(): db for db in self.available_databases}
            
            if selected_db != "none" and selected_db in available_dbs:
                actual_db_name = available_dbs[selected_db]
                
                # Close existing connections if any
                if hasattr(self, 'db') and self.db is not None:
                    try:
                        self.db.dispose()
                    except Exception as e:
                        print(f"Warning: Error disposing old connection: {str(e)}")
                
                try:
                    # Create new connection string with specific database
                    username = os.getenv("DB_USER", "harsha")
                    password = quote_plus(os.getenv("DB_PASSWORD", "HarshaV@123"))
                    host = os.getenv("DB_HOST", "localhost")
                    port = os.getenv("DB_PORT", "3306")
                    
                    connection_string = f"mysql+pymysql://{username}:{password}@{host}:{port}/{actual_db_name}"
                    specific_engine = create_engine(
                        connection_string,
                        pool_pre_ping=True,
                        pool_recycle=3600,
                        pool_size=5,
                        max_overflow=10
                    )
                    
                    # Test connection with timeout
                    with specific_engine.connect() as conn:
                        result = conn.execute(text("SELECT 1"))
                        if not result:
                            raise Exception("Connection test failed")
                    
                    # Initialize SQLDatabase instance
                    self.db = SQLDatabase(specific_engine, actual_db_name)
                    self.selected_db_name = actual_db_name
                    
                    # Update mode tracker
                    self.mode_tracker.set_db_connection(True)
                    
                    # Initialize toolkit and agent executor
                    toolkit = SQLDatabaseToolkit(db=self.db, llm=self.llm)
                    
                    # Get the latest prompt template
                    try:
                        prompt_template = hub.pull("langchain-ai/sql-agent-system-prompt")
                        system_message = prompt_template.format(dialect="MySQL", top_k=5)
                    except Exception as e:
                        print(f"Warning: Error pulling prompt template: {str(e)}")
                        # Fallback to basic system message
                        system_message = "You are an agent that helps users interact with MySQL databases."
                    
                    # Create new agent executor
                    self.agent_executor = create_react_agent(
                        self.llm,
                        toolkit.get_tools(),
                        state_modifier=system_message
                    )
                    
                    print(f"Successfully switched to database: {actual_db_name}")
                    return True
                    
                except Exception as e:
                    print(f"Error during database connection: {str(e)}")
                    # Reset connection state
                    self.db = None
                    self.selected_db_name = None
                    self.mode_tracker.set_db_connection(False)
                    return False
                    
            else:
                print(f"No matching database found for: {query_or_db_name}")
                return False
                
        except Exception as e:
            print(f"Error in handle_database_switch: {str(e)}")
            return False
    async def handle_creative_request(self, query: str) -> str:
        creative_prompt = f"""
        Input: "{query}"
        Task: Generate creative content in concise words
        Rules:
        - Focus on imagination and originality
        - Avoid technical/database references
        - Maintain consistent style/theme
        - Follow user's specified format (poem/story/etc)
        - Keep creative elements cohesive
        -limited to 1-2 sentences and no more than 120 characters
        Context: Previous creative outputs: {self.conversation_memory.load_memory_variables({"input": query}).get('history', '')}
        Generate:"""

        return await self.get_llm_response(creative_prompt)

    async def handle_explanation_request(self, query: str) -> str:
        explanation_prompt = f"""
        Question: "{query}"
        Task: Explain concept/process
        Rules:
        - Clear, concise explanation
        - Use relevant examples
        - Break down complex ideas
        - Focus on key points
        - Avoid unnecessary jargon
        -limited to 1-2 sentences and no more than 120 characters
        Previous context: {self.conversation_memory.load_memory_variables({"input": query}).get('history', '')}
        Explain:"""

        return await self.get_llm_response(explanation_prompt)

    async def handle_transition(self, query: str) -> str:
        transition_prompt = f"""
        Current query: "{query}"
        From mode: {self.mode_tracker.previous_mode}
        To mode: {self.mode_tracker.current_mode}
        Task: Smooth topic transition
        Rules:
        - Acknowledge topic change
        - Bridge previous and new topics
        - Clear closure of previous topic
        - Set context for new topic
        Previous context: {self.conversation_memory.load_memory_variables({"input": query}).get('history', '')}
        Response:"""

        return await self.get_llm_response(transition_prompt)
    async def handle_general_conversation(self, query: str) -> str:
        # Load full conversation history
        memory_context = self.conversation_memory.load_memory_variables({"input": query})
        chat_history = memory_context.get('history', '')
        conversation_prompt = f"""
        User query: "{query}"
        Full conversation history: {chat_history}
        
        Respond naturally as VOX, focusing only on the current user query.
        - For creative requests: Generate fresh content in less words
        - For questions: Provide direct answers
        - For context questions: Reference only relevant history
        - Avoid mentioning database details unless explicitly asked
        -limited to 1-2 sentences and no more than 120 characters
        """
        
        response = await self.get_llm_response(conversation_prompt)
        
        # Save context for all conversations
        self.conversation_memory.save_context(
            {"input": query},
            {"output": response}
        )
        
        return response

async def main():
    # Load environment variables
    load_dotenv()
    
    # Get API keys from environment variables
    dg_api_key = os.getenv("DEEPGRAM_API_KEY")
    openai_api_key = os.getenv("OPENAI_API_KEY")
    
    # Verify required environment variables
    if not all([dg_api_key, openai_api_key]):
        print("Missing required API keys in environment variables")
        return

    try:
        assistant = VoiceSQLAssistant(dg_api_key, openai_api_key)
        
        # Select database
        if not await assistant.select_database():
            print("Failed to select database. Exiting...")
            return
        
        # Main interaction loop with graceful interruption handling
        while True:
            try:
                print("\nReady for your query (speak clearly)... Press Ctrl+C to exit")
                query = await assistant.listen_for_speech()
                
                if not query:
                    print("No speech detected. Please try again.")
                    continue
                    
                if query.lower() in ['quit', 'exit', 'bye', 'goodbye', 'thank you', 'thanks thats it']:
                    print("\nGoodbye! Have a great day!")
                    break
                    
                response = await assistant.process_query(query)
                if response:
                    print("\n" + response)
                
            except KeyboardInterrupt:
                print("\n\nGracefully shutting down... Please wait.")
                break
            except Exception as e:
                print(f"\nError in main loop: {str(e)}")
                print("Continuing to next interaction...")
                continue
    
    except KeyboardInterrupt:
        print("\n\nShutting down due to user interrupt...")
    except Exception as e:
        print(f"\nFatal error: {str(e)}")
    finally:
        # Cleanup code
        print("\nClosing all connections...")
        try:
            if hasattr(assistant, 'engine'):
                assistant.engine.dispose()
        except Exception as e:
            print(f"Error during cleanup: {str(e)}")
        print("Goodbye!")

if __name__ == "__main__":
    asyncio.run(main())
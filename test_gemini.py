import os
from dotenv import load_dotenv
from google import genai

# 1. Load the environment variables from the .env file
load_dotenv()

# 2. Initialize the client. 
# It automatically looks for the GEMINI_API_KEY environment variable we set in Step 4.
client = genai.Client()

def test_connection():
    print("Sending prompt to Gemini...")
    
    # 3. Call the API using the newest Gemini 2.5 Flash model
    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents="Hello Gemini! You are going to be the brain of my new game-testing swarm. Please introduce yourself in one sentence."
    )
    
    print("\nResponse from Gemini:")
    print(response.text)

if __name__ == "__main__":
    test_connection()
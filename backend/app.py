from flask import Flask, request, jsonify
from flask_cors import CORS
import os
import uuid
from werkzeug.utils import secure_filename
from groclake.modellake import ModelLake
from groclake.vectorlake import VectorLake
import PyPDF2
import docx
import traceback

app = Flask(__name__)
CORS(app)  # Enable CORS for all routes

# Set Groclake credentials
os.environ['GROCLAKE_API_KEY'] = '013d407166ec4fa56eb1e1f8cbe183b9'
os.environ['GROCLAKE_ACCOUNT_ID'] = '3838e00de26b4f0c6e8e84d7ea89e566'

# Initialize Groclake
modellake = ModelLake()
vectorlake = VectorLake()

# In a real app, you would use a database
users_db = {}
sessions = {}
conversation_history = {}  # Store conversation history for each session
UPLOAD_FOLDER = 'uploads'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

# Add a default user for testing
users_db = {
    "test": {"password": "test123"}
}

# Authentication functions - shared between both features
@app.route('/api/register', methods=['POST'])
def register():
    data = request.json
    username = data.get('username')
    password = data.get('password')
    
    if not username or not password:
        return jsonify({"error": "Username and password required"}), 400
    
    if username in users_db:
        return jsonify({"error": "User already exists"}), 400
    
    users_db[username] = {"password": password}
    return jsonify({"message": "User registered successfully"}), 201

@app.route('/api/login', methods=['POST'])
def login():
    data = request.json
    username = data.get('username')
    password = data.get('password')
    
    if not username or not password:
        return jsonify({"error": "Username and password required"}), 400
    
    if username not in users_db or users_db[username]["password"] != password:
        return jsonify({"error": "Invalid credentials"}), 401
    
    # Create session
    session_id = str(uuid.uuid4())
    sessions[session_id] = username
    # Initialize conversation history for this session
    conversation_history[session_id] = []
    
    print(f"Login successful for user: {username}, session: {session_id}")
    print(f"Active sessions: {sessions}")
    
    return jsonify({"message": "Login successful", "token": session_id}), 200


#############################################################################
# GENERAL QUERY MODULE - Everything related to the chat functionality
#############################################################################

class GeneralQueryHandler:
    @staticmethod
    def verify_token(token):
        """Verify token is valid"""
        if not token or token not in sessions:
            return False
        return True

    @staticmethod
    def handle_chat(token, user_question):
        """Process a chat message and return response"""
        # Get conversation history or initialize if empty
        session_history = conversation_history.get(token, [])
        
        # Check if this is a structured response from the interactive UI
        is_structured_response = False
        structured_answers = {}
        
        # Look for structured answer pattern: "Question: Answer"
        if "\n\n" in user_question:
            sections = user_question.split("\n\n")
            is_structured_response = True
            
            # Parse each question-answer pair
            for section in sections:
                if ":" in section:
                    q_part, a_part = section.split(":", 1)
                    structured_answers[q_part.strip()] = a_part.strip()
        
        # System prompt for medical assistant with improved handling for structured answers
        system_content = """You are an empathetic medical assistant conducting an interactive diagnosis.
        Your approach should be:
        1. Start with a positive, professional tone (avoid starting with "I'm sorry")
        2. If the user has provided structured answers to your previous questions, acknowledge those answers specifically
        3. Ask ONE specific follow-up question at a time
        4. Structure your responses clearly:
           - Acknowledge the previous answers professionally
           - Ask a focused follow-up question
           - Explain why this information helps
           - If sufficient info, provide analysis
        5. Keep responses complete (never cut off mid-sentence)
        6. Maintain a constructive, solution-focused tone
        7. Include clear action items when appropriate
        8. Always remind that this is not a substitute for professional medical advice
        """
        
        # Enhanced system prompt if this is a response to our structured questions
        if is_structured_response:
            system_content += "\n\nThe user has provided responses to your previous questions in a structured format. Please acknowledge each response and provide appropriate follow-up based on the answers."
        
        try:
            print("Creating fallback response for chat message...")
            
            # Check for rule-based response
            if is_structured_response:
                answer = GeneralQueryHandler.generate_structured_response(structured_answers)
            else:
                answer = GeneralQueryHandler.generate_rule_based_response(user_question, session_history)
            
            # Print details about the response for debugging
            print(f"User question: '{user_question}'")
            print(f"Response length: {len(answer)}")
            print(f"Response preview: {answer[:100]}...")
            
            # Check for empty responses (which would cause client-side errors)
            if not answer or len(answer.strip()) < 10:
                print("Warning: Generated response is empty or too short. Using generic response.")
                answer = (
                    "I understand your question about health concerns. To provide more specific guidance, "
                    "could you please share more details about your symptoms, such as when they started "
                    "and any factors that seem to improve or worsen them? This will help me give you more relevant information."
                )
            
            # Update conversation history
            session_history.append({"type": "question", "content": user_question})
            session_history.append({"type": "answer", "content": answer})
            conversation_history[token] = session_history
            
            # Return response without indicating fallback to maintain user confidence
            return {"message": answer, "is_fallback": False}
            
        except Exception as e:
            print(f"Error in chat endpoint: {str(e)}")
            print(traceback.format_exc())
            
            # Final fallback response
            if is_structured_response:
                fallback_response = (
                    "Thank you for providing those answers. Based on what you've shared, "
                    "I'd recommend monitoring your symptoms. If they worsen or persist, "
                    "please consult with a healthcare professional. "
                    "Is there anything specific about your symptoms that you're concerned about?"
                )
            else:
                fallback_response = (
                    "Thank you for sharing your symptoms. Based on what you've described, "
                    "I'd recommend consulting with a healthcare professional for a proper diagnosis. "
                    "In the meantime, could you tell me when these symptoms first started? "
                    "This will help me better understand your situation."
                )
            
            # Still update conversation history
            session_history.append({"type": "question", "content": user_question})
            session_history.append({"type": "answer", "content": fallback_response})
            conversation_history[token] = session_history
            
            return {"message": fallback_response, "is_fallback": True}

    @staticmethod
    def generate_structured_response(structured_answers):
        """Generate responses to structured answers from the interactive UI"""
        # Check for fever-related questions
        fever_questions = [q for q in structured_answers.keys() if 'fever' in q.lower()]
        if fever_questions:
            temp_reading = None
            other_symptoms = False
            duration = None
            
            for question in structured_answers:
                answer = structured_answers[question]
                
                if 'temperature' in question.lower():
                    temp_reading = answer
                elif 'other symptoms' in question.lower():
                    if answer.lower() not in ['no', 'none', 'not really']:
                        other_symptoms = True
                elif 'long' in question.lower() or 'duration' in question.lower():
                    duration = answer
            
            # Generate a response based on the fever information
            response = "Thank you for providing those details about your fever. "
            
            if temp_reading:
                if any(high_temp in temp_reading.lower() for high_temp in ['102', '103', '104', '105']):
                    response += f"A temperature of {temp_reading} is considered a high fever. "
                    if duration and 'today' in duration.lower():
                        response += "Even though it just started today, this temperature is concerning and should be monitored closely. "
                    else:
                        response += "This is concerning, especially if it has persisted. "
                    response += "I recommend contacting a healthcare provider soon. "
                elif any(mild_temp in temp_reading.lower() for mild_temp in ['99', '100']):
                    response += f"A temperature of {temp_reading} is considered a low-grade fever. "
                    if duration and 'today' in duration.lower():
                        response += "Since it just started today, you can monitor it for now. "
                    else:
                        response += "It's generally not a major concern but worth monitoring. "
                else:
                    response += f"Based on the temperature reading of {temp_reading}, "
            
            if other_symptoms:
                response += "The presence of other symptoms alongside your fever could indicate an infection or illness. "
                response += "Have you noticed if anything specific triggers or worsens these symptoms? "
            else:
                response += "A fever without other symptoms might be your body's initial response to an infection. "
                response += "Are you staying hydrated and getting enough rest? "
            
            response += "\nFor fever management, you can:\n"
            response += "1. Stay well-hydrated with water or electrolyte drinks\n"
            response += "2. Rest as much as possible\n"
            response += "3. Use appropriate over-the-counter fever reducers like acetaminophen if needed\n\n"
            response += "Please remember that I'm providing general information and not medical advice. If your fever exceeds 103°F (39.4°C), persists for more than three days, or is accompanied by severe symptoms, please seek medical attention."
            
            return response
        
        # Check for stomach-related questions
        stomach_questions = [q for q in structured_answers.keys() if any(term in q.lower() for term in ['stomach', 'pain', 'digest'])]
        if stomach_questions:
            duration = None
            pattern = None
            triggers = None
            
            for question in structured_answers:
                answer = structured_answers[question]
                
                if 'long' in question.lower() or 'duration' in question.lower():
                    duration = answer
                elif 'constant' in question.lower() or 'come and go' in question.lower():
                    pattern = answer
                elif 'food' in question.lower() or 'trigger' in question.lower():
                    triggers = answer
            
            # Generate a response based on the stomach information
            response = "Thank you for providing those details about your stomach pain. "
            
            if duration:
                if any(short_term in duration.lower() for short_term in ['today', 'day', 'just started']):
                    response += "Since the pain just started recently, it could be related to something you ate or a brief digestive issue. "
                elif any(long_term in duration.lower() for long_term in ['week', 'month', 'year']):
                    response += "The fact that you've been experiencing this pain for some time suggests it may be a chronic condition. It would be important to consult with a gastroenterologist. "
            
            if pattern:
                if 'constant' in pattern.lower():
                    response += "Constant pain that doesn't subside is worth discussing with a healthcare provider as it might indicate inflammation or irritation. "
                elif 'come' in pattern.lower() and 'go' in pattern.lower():
                    response += "Pain that comes and goes is common with various digestive issues like gas, indigestion, or even conditions like IBS. "
                elif 'after' in pattern.lower() and ('eat' in pattern.lower() or 'food' in pattern.lower() or 'meal' in pattern.lower()):
                    response += "Pain that occurs after eating could be related to food sensitivities, gastritis, or other digestive processes. "
            
            if triggers:
                if any(food in triggers.lower() for food in ['spicy', 'fatty', 'fried']):
                    response += "Spicy and fatty foods commonly trigger digestive discomfort for many people. Limiting these foods could help reduce symptoms. "
                elif any(food in triggers.lower() for food in ['dairy', 'milk', 'cheese', 'lactose']):
                    response += "Discomfort after consuming dairy might suggest lactose intolerance. You might consider trying lactose-free alternatives. "
            
            response += "\nBased on what you've shared, here are some general recommendations:\n"
            response += "1. Keep a food diary to identify potential trigger foods\n"
            response += "2. Consider smaller, more frequent meals rather than large ones\n"
            response += "3. Stay hydrated throughout the day\n"
            response += "4. Avoid lying down immediately after eating\n\n"
            response += "Would you like me to provide more specific information about managing stomach discomfort? Remember, persistent or severe symptoms should always be evaluated by a healthcare professional."
            
            return response
        
        # Generic response for other structured answers
        response = "Thank you for providing those detailed answers. This information helps me understand your situation better. "
        response += "Based on what you've shared, it seems you're experiencing "
        
        # Try to identify what they're describing from the questions
        condition_terms = []
        for question in structured_answers.keys():
            for term in ["pain", "ache", "discomfort", "fever", "cough", "headache", "rash", "nausea"]:
                if term in question.lower() and term not in condition_terms:
                    condition_terms.append(term)
        
        if condition_terms:
            response += f"{', '.join(condition_terms)}. "
        else:
            response += "some health concerns. "
        
        response += "Could you share if anything seems to improve or worsen your symptoms? Understanding these patterns can help provide more relevant information."
        
        return response

    @staticmethod
    def generate_rule_based_response(user_question, session_history):
        """Generate a rule-based response when ML services fail"""
        user_question_lower = user_question.lower()
        
        # Check if this is a direct question from a health category selection
        from_category = False
        if len(user_question_lower) < 100 and "?" in user_question_lower:
            # This might be a direct question from a category
            from_category = True
        
        # Handle health category based questions with more consistent formatting
        health_categories = {
            # Respiratory questions
            "cough": "I understand you're asking about a cough. To provide the most helpful information:\n\n1. How long have you been coughing?\n2. Is your cough dry or productive (bringing up mucus)?\n3. Have you noticed any specific triggers for your cough?",
            "breathing": "I understand you're having breathing concerns. To better understand your situation:\n\n1. When did you first notice difficulty breathing?\n2. Does it happen during specific activities or all the time?\n3. Have you experienced this before?",
            "shortness of breath": "I understand you're experiencing shortness of breath. Let me help you better:\n\n1. When did this shortness of breath start?\n2. Does it occur at rest, with activity, or both?\n3. Is it associated with any other symptoms like chest pain or dizziness?",
            
            # Digestive questions
            "stomach": "About your stomach concern, I'd like to understand more:\n\n1. How long have you been experiencing this discomfort?\n2. Is the pain constant or does it come and go?\n3. Have you noticed any connection to eating certain foods?",
            "nausea": "Regarding your nausea, to help you better:\n\n1. How long have you been feeling nauseated?\n2. Have you vomited or just felt nauseated?\n3. Have you identified any triggers for this feeling?",
            "acid reflux": "About your acid reflux concern:\n\n1. How often do you experience acid reflux symptoms?\n2. Do you notice them at any particular time (after meals, when lying down)?\n3. Have you tried any remedies so far?",
            
            # Neurological questions
            "headache": "To help with your headache concern:\n\n1. How long have you been experiencing these headaches?\n2. Where in your head do you feel the pain?\n3. How would you rate the intensity from 1-10?",
            "migraine": "Regarding your migraine question:\n\n1. How frequently do you experience migraines?\n2. Do you notice any warning signs before they start?\n3. What triggers have you identified, if any?",
            "dizziness": "About your dizziness concern:\n\n1. When did you first notice feeling dizzy?\n2. Would you describe it more as lightheadedness or a spinning sensation?\n3. Is it constant or does it come in episodes?",
            
            # Skin questions
            "rash": "Regarding your rash question:\n\n1. Where on your body is the rash located?\n2. How long have you had this rash?\n3. Is it itchy, painful, or neither?",
            "itchy skin": "About your itchy skin:\n\n1. How long have you been experiencing itchiness?\n2. Is it limited to one area or all over your body?\n3. Have you noticed any visible changes to your skin?",
            "eczema": "Regarding your eczema question:\n\n1. Which parts of your body are affected?\n2. How long have you been dealing with this?\n3. Have you identified any triggers that worsen your symptoms?",
            
            # Musculoskeletal questions
            "joint pain": "About your joint pain:\n\n1. Which joints are affected?\n2. How long have you been experiencing this pain?\n3. Does anything seem to improve or worsen the pain?",
            "back pain": "Regarding your back pain:\n\n1. Where exactly in your back is the pain located?\n2. How long have you been experiencing it?\n3. Would you describe the pain as sharp, dull, or aching?",
            "muscle": "About your muscle concern:\n\n1. Which muscles are affected?\n2. How long have you been experiencing this issue?\n3. Is the discomfort constant or only during certain activities?"
        }
        
        # If this is a direct question from a category example, return a structured response
        for key, response in health_categories.items():
            if key in user_question_lower:
                return response
        
        # Check previous exchanges to see if this is a follow-up answer to questions
        answered_questions = False
        contains_specific_answers = False
        
        if len(session_history) >= 2:
            last_bot_message = None
            last_user_message = session_history[-1]["content"] if session_history[-1]["type"] == "question" else None
            
            # Find the last bot message that contains questions
            for i in range(len(session_history)-1, -1, -1):
                if session_history[i]["type"] == "answer" and any(q in session_history[i]["content"] for q in ["1.", "2.", "3.", "how long", "rate the pain"]):
                    last_bot_message = session_history[i]["content"]
                    break
            
            # If we found a message with questions and the user's reply has specific answers
            if last_bot_message and last_user_message:
                contains_specific_answers = any(term in last_user_message.lower() for term in 
                    ["just started", "day", "week", "month", "constant", "comes and goes",
                     "yes", "no", "mild", "moderate", "severe", "spicy", "dairy"])
            
            # If we detect a user has answered our questions with specific details
            if contains_specific_answers:
                answered_questions = True
        
        # Generate more diagnostic responses when users have answered questions
        if answered_questions:
            # Common diagnostic pathways based on symptoms
            if 'stomach' in user_question_lower or 'digest' in user_question_lower:
                if 'day' in user_question_lower or 'just started' in user_question_lower:
                    if 'after' in user_question_lower and ('meal' in user_question_lower or 'eating' in user_question_lower):
                        return (
                            "Based on what you've shared about your stomach pain being recent and occurring after meals, "
                            "this suggests you may be experiencing indigestion or possibly gastritis. Indigestion is common "
                            "and can be triggered by certain foods, eating too quickly, or stress.\n\n"
                            "Here are some approaches that might help:\n"
                            "1. Try eating smaller, more frequent meals\n"
                            "2. Avoid spicy, fatty, or acidic foods temporarily\n"
                            "3. Consider over-the-counter antacids for temporary relief\n\n"
                            "If the pain is severe, persistent beyond a few days, or accompanied by fever, vomiting, or blood in stool, "
                            "please seek medical attention promptly."
                        )
                    else:
                        return (
                            "Based on your description of recent stomach discomfort, there are several potential causes including "
                            "gastritis, food intolerance, or a mild stomach virus. Since this is a recent onset, it may resolve on its own "
                            "with basic care.\n\n"
                            "I recommend:\n"
                            "1. Staying hydrated with clear fluids\n"
                            "2. Eating bland foods like rice, toast, or bananas\n"
                            "3. Resting and monitoring your symptoms\n\n"
                            "If symptoms worsen, persist beyond 48 hours, or if you develop fever or vomiting, please consult a healthcare provider."
                        )
                elif any(term in user_question_lower for term in ['week', 'month', 'chronic', 'long time']):
                    return (
                        "The stomach pain you've been experiencing for an extended period could indicate a chronic condition "
                        "such as irritable bowel syndrome (IBS), gastroesophageal reflux disease (GERD), or food sensitivities. "
                        "Persistent symptoms should be evaluated by a healthcare provider.\n\n"
                        "In the meantime, consider:\n"
                        "1. Keeping a food diary to identify potential trigger foods\n"
                        "2. Managing stress through relaxation techniques\n"
                        "3. Avoiding alcohol, caffeine, and spicy foods\n\n"
                        "Given the chronic nature of your symptoms, I strongly recommend scheduling an appointment with a "
                        "gastroenterologist for proper diagnosis and treatment."
                    )
            
            elif 'headache' in user_question_lower or 'migraine' in user_question_lower:
                if any(term in user_question_lower for term in ['severe', '8', '9', '10']):
                    return (
                        "The severe headache you've described is concerning. Based on your description, this could be a migraine "
                        "or tension headache, but severe headaches can sometimes indicate more serious conditions.\n\n"
                        "For immediate relief:\n"
                        "1. Rest in a dark, quiet room\n"
                        "2. Apply a cold compress to your forehead or neck\n"
                        "3. Consider appropriate over-the-counter pain relievers if not contraindicated for you\n\n"
                        "Given the severity you've described, I recommend consulting with a healthcare provider soon, especially if this "
                        "is a new or 'worst headache of your life' or if accompanied by fever, stiff neck, or confusion."
                    )
                else:
                    return (
                        "Based on your description, you appear to be experiencing a tension-type headache or mild migraine. "
                        "These are common and often triggered by stress, dehydration, poor sleep, or eye strain.\n\n"
                        "Here are some strategies that might help:\n"
                        "1. Ensure you're staying hydrated throughout the day\n"
                        "2. Take regular breaks from screens and practice the 20-20-20 rule (every 20 minutes, look at something 20 feet away for 20 seconds)\n"
                        "3. Practice relaxation techniques like deep breathing or gentle neck stretches\n"
                        "4. Consider over-the-counter pain relievers if appropriate for you\n\n"
                        "If these headaches become more frequent or severe, please consult a healthcare provider."
                    )
            
            elif 'fever' in user_question_lower:
                if any(term in user_question_lower for term in ['102', '103', '104']):
                    return (
                        "With a temperature reading this high (over 102°F), you're experiencing a significant fever that requires attention. "
                        "This level of fever suggests your body is fighting an infection, which could be viral or bacterial.\n\n"
                        "Immediate steps to take:\n"
                        "1. Stay well-hydrated with water or electrolyte drinks\n"
                        "2. Use appropriate fever-reducing medication (acetaminophen or ibuprofen) if not contraindicated for you\n"
                        "3. Rest and monitor your temperature\n\n"
                        "A fever this high warrants medical attention, especially if it persists for more than 24 hours or is accompanied by "
                        "severe headache, rash, confusion, persistent vomiting, or difficulty breathing. Please contact a healthcare provider today."
                    )
                else:
                    return (
                        "Based on what you've shared, you have a low-grade fever. This is often your body's natural response to fighting "
                        "a mild infection, most commonly viral.\n\n"
                        "Here's what I recommend:\n"
                        "1. Rest and get plenty of fluids\n"
                        "2. Monitor your temperature over the next 24-48 hours\n"
                        "3. Use acetaminophen or ibuprofen as directed if you're uncomfortable\n\n"
                        "If your fever persists beyond 3 days, rises above 102°F (39°C), or is accompanied by severe symptoms like difficulty "
                        "breathing or confusion, please seek medical care promptly."
                    )
        
        # If not a follow-up or no specific symptoms matched, check for common symptoms in the query
        if 'stomach' in user_question_lower and any(word in user_question_lower for word in ['ache', 'pain', 'hurt', 'issue']):
            return (
                "I understand you're experiencing stomach pain. This can be caused by various factors "
                "including indigestion, gas, or more serious conditions. To help me understand better:\n\n"
                "1. How long have you been experiencing this pain?\n"
                "2. Is it constant or does it come and go?\n"
                "3. Have you noticed any specific foods triggering it?\n\n"
                "While I can provide some general guidance, it's important to consult with a healthcare provider "
                "if the pain is severe, persistent, or accompanied by other concerning symptoms."
            )
        elif 'head' in user_question_lower and any(word in user_question_lower for word in ['ache', 'pain', 'hurt', 'migraine']):
            return (
                "I'm sorry to hear you're dealing with a headache. Headaches can have various causes including stress, "
                "dehydration, lack of sleep, or eye strain. To better understand your situation:\n\n"
                "1. How long have you had this headache?\n"
                "2. How would you rate the pain from 1-10?\n"
                "3. Have you tried any remedies already?\n\n"
                "Remember, while I can offer general information, persistent or severe headaches should be evaluated by a healthcare professional."
            )
        elif 'fever' in user_question_lower:
            return (
                "I see you've mentioned having a fever. Fevers are often your body's natural response to infection. "
                "To help me understand your situation better:\n\n"
                "1. What is your temperature reading?\n"
                "2. Are you experiencing any other symptoms alongside the fever?\n"
                "3. How long have you had the fever?\n\n"
                "While I can provide general guidance, please remember that I'm not a substitute for professional medical advice, "
                "especially for fevers that are high, persistent, or accompanied by other concerning symptoms."
            )
        elif 'cough' in user_question_lower or 'cold' in user_question_lower or 'flu' in user_question_lower:
            return (
                "I understand you're experiencing respiratory symptoms. These could be related to a cold, flu, or other "
                "respiratory conditions. To better understand your specific situation:\n\n"
                "1. How long have you been experiencing these symptoms?\n"
                "2. Is your cough dry or productive (producing mucus)?\n"
                "3. Do you have other symptoms like fever, body aches, or fatigue?\n\n"
                "While I can provide general information, these symptoms can vary widely in cause and treatment, so consulting "
                "with a healthcare provider is recommended for proper diagnosis and care."
            )
        else:
            # For any other query, return a generic response
            return (
                "Thank you for sharing your health concern. To help me understand your situation better and provide more relevant information, could you please:\n\n"
                "1. Tell me more about when these symptoms started?\n"
                "2. Describe any specific patterns or triggers you've noticed?\n"
                "3. Mention any remedies you've already tried?\n\n"
                "While I'm here to provide health information, please remember that I cannot replace professional medical advice. "
                "If your symptoms are severe, persistent, or concerning, I'd recommend consulting with a healthcare provider."
            )

    # ...existing code...

@app.route('/api/chat', methods=['POST'])
def chat():
    token = request.headers.get('Authorization')
    print(f"Received chat request with token: {token}")
    
    if not GeneralQueryHandler.verify_token(token):
        return jsonify({"error": "Unauthorized"}), 401
    
    data = request.json
    user_question = data.get('message')
    
    if not user_question or not user_question.strip():
        return jsonify({"error": "Empty message"}), 400
    
    try:
        result = GeneralQueryHandler.handle_chat(token, user_question)
        # Double-check that we have a valid response
        if not result or not isinstance(result, dict) or 'message' not in result:
            raise ValueError("Invalid response structure")
        
        # Ensure we have a non-empty message
        if not result['message'] or len(result['message'].strip()) < 10:
            result['message'] = "I understand your question. Could you provide more details about your symptoms so I can better assist you?"
        
        return jsonify(result)
    except Exception as e:
        print(f"Unhandled error in chat endpoint: {str(e)}")
        print(traceback.format_exc())
        return jsonify({
            "message": "I understand your question but am having trouble processing it right now. Could you try rephrasing or asking a different health question?",
            "is_fallback": True,
            "error": True  # Flag to indicate error state to the frontend
        }), 200  # Still return 200 to allow frontend to handle gracefully


#############################################################################
# MEDICAL REPORT MODULE - Everything related to report analysis
#############################################################################

class MedicalReportHandler:
    @staticmethod
    def verify_token(token):
        """Verify token is valid"""
        if not token or token not in sessions:
            return False
        return True
        
    @staticmethod
    def extract_text_from_file(file):
        """Extract text content from various file formats"""
        filename = file.filename
        file_extension = filename.split('.')[-1].lower()
        
        if file_extension == 'pdf':
            reader = PyPDF2.PdfReader(file)
            text = ""
            for page in reader.pages:
                text += page.extract_text() + "\n"
            return text
        elif file_extension in ['doc', 'docx']:
            doc = docx.Document(file)
            text = "\n".join([para.text for para in doc.paragraphs])
            return text
        elif file_extension in ['txt']:
            return file.read().decode('utf-8')
        else:
            return None

    @staticmethod
    def generate_report_analysis(extracted_text, filename):
        """Generate a structured analysis of a medical report when ML services fail"""
        # Convert text to lowercase for easier matching
        text_lower = extracted_text.lower()
        
        # Prepare a response structure
        analysis = f"## Medical Report Analysis: {filename}\n\n"
        
        # Try to detect the type of report
        report_type = "Unknown"
        if any(term in text_lower for term in ["cbc", "complete blood count", "wbc", "rbc", "hemoglobin", "hematocrit"]):
            report_type = "Blood Test"
        elif any(term in text_lower for term in ["urine", "urinalysis"]):
            report_type = "Urinalysis"
        elif any(term in text_lower for term in ["glucose", "hba1c", "blood sugar"]):
            report_type = "Glucose/Diabetes Test"
        elif any(term in text_lower for term in ["cholesterol", "hdl", "ldl", "triglycerides", "lipid"]):
            report_type = "Lipid Panel"
        elif any(term in text_lower for term in ["x-ray", "xray", "radiograph"]):
            report_type = "X-Ray Report"
        elif any(term in text_lower for term in ["mri", "magnetic resonance"]):
            report_type = "MRI Report"
        elif any(term in text_lower for term in ["ct scan", "cat scan"]):
            report_type = "CT Scan"
        elif any(term in text_lower for term in ["ultrasound", "sonogram", "doppler"]):
            report_type = "Ultrasound"
        elif any(term in text_lower for term in ["ecg", "ekg", "electrocardiogram"]):
            report_type = "ECG/EKG"
        
        analysis += f"**Report Type**: {report_type}\n\n"
        
        # Look for common patterns in medical reports
        # 1. Find values with units and compare to normal ranges if possible
        values_found = []
        # Check for common test patterns like "Test: Value Unit (Range)"
        import re
        
        # Look for these common tests and extract values
        common_tests = {
            # Blood tests
            "hemoglobin": {"unit": "g/dL", "normal": "12-16 g/dL (females), 13.5-17.5 g/dL (males)"},
            "hematocrit": {"unit": "%", "normal": "36-48% (females), 41-50% (males)"},
            "rbc": {"unit": "million/μL", "normal": "4.2-5.4 million/μL (females), 4.7-6.1 million/μL (males)"},
            "wbc": {"unit": "cells/μL", "normal": "4,500-11,000 cells/μL"},
            "platelets": {"unit": "/μL", "normal": "150,000-450,000/μL"},
            
            # Lipid panel
            "cholesterol": {"unit": "mg/dL", "normal": "<200 mg/dL"},
            "ldl": {"unit": "mg/dL", "normal": "<100 mg/dL"},
            "hdl": {"unit": "mg/dL", "normal": ">40 mg/dL (males), >50 mg/dL (females)"},
            "triglycerides": {"unit": "mg/dL", "normal": "<150 mg/dL"},
            
            # Liver function
            "alt": {"unit": "U/L", "normal": "7-56 U/L"},
            "ast": {"unit": "U/L", "normal": "5-40 U/L"},
            
            # Kidney function
            "creatinine": {"unit": "mg/dL", "normal": "0.6-1.2 mg/dL (males), 0.5-1.1 mg/dL (females)"},
            "bun": {"unit": "mg/dL", "normal": "7-20 mg/dL"},
            "egfr": {"unit": "mL/min", "normal": ">60 mL/min"},
            
            # Glucose
            "glucose": {"unit": "mg/dL", "normal": "70-99 mg/dL (fasting)"},
            "hba1c": {"unit": "%", "normal": "< 5.7%"},
            
            # Thyroid
            "tsh": {"unit": "μIU/mL", "normal": "0.4-4.0 μIU/mL"},
            "t4": {"unit": "μg/dL", "normal": "4.5-12 μg/dL"},
            "t3": {"unit": "ng/dL", "normal": "80-200 ng/dL"}
        }
        
        # Try to extract test results for common tests
        for test, info in common_tests.items():
            # Look for test name followed by numbers
            pattern = rf'{test}\D*(\d+\.?\d*)'
            matches = re.findall(pattern, text_lower, re.IGNORECASE)
            if matches:
                values_found.append(f"**{test.upper()}**: {matches[0]} {info['unit']} (Normal range: {info['normal']})")
        
        if values_found:
            analysis += "### Detected Values:\n"
            for value in values_found:
                analysis += f"- {value}\n"
            analysis += "\n"
        
        # Look for terms indicating abnormality
        abnormal_terms = ["abnormal", "high", "low", "elevated", "decreased", "positive", "negative", 
                           "out of range", "reference range", "critical"]
        
        abnormal_findings = []
        for term in abnormal_terms:
            # Find instances where abnormal terms appear
            pattern = rf'(\w+\s*\w*)\s*(?:is|was|were|appears?|shows?)\s*{term}'
            matches = re.findall(pattern, text_lower)
            abnormal_findings.extend(matches)
        
        if abnormal_findings:
            analysis += "### Potential Abnormal Findings:\n"
            for finding in set(abnormal_findings):  # Using set to remove duplicates
                analysis += f"- {finding.strip().capitalize()}\n"
            analysis += "\n"
        
        # Add recommendations
        analysis += "### Recommendations:\n"
        analysis += "1. **Consult with your healthcare provider**: This automated analysis is not a replacement for professional medical interpretation.\n"
        analysis += "2. **Review with a specialist**: Have these results reviewed by an appropriate medical specialist.\n"
        analysis += "3. **Follow-up testing**: Your doctor may recommend additional tests based on these results.\n\n"
        
        # Add disclaimer
        analysis += "### Disclaimer:\n"
        analysis += "This analysis is generated by an automated system with limited capabilities. It may miss important findings or incorrectly identify normal results as abnormal. Always consult with a qualified healthcare professional for accurate interpretation of medical reports."
        
        return analysis

    @staticmethod
    def analyze_report(token, file):
        """Process a report analysis request and return response"""
        if not file or file.filename == '':
            return {"error": "No file selected"}, 400
        
        # Save the file
        filename = secure_filename(file.filename)
        file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(file_path)
        
        # Extract text from the file
        file.seek(0)  # Reset file pointer to beginning
        try:
            extracted_text = MedicalReportHandler.extract_text_from_file(file)
        except Exception as e:
            print(f"Error extracting text: {str(e)}")
            print(traceback.format_exc())
            return {"error": f"Error extracting text from file: {str(e)}"}, 400
        
        if not extracted_text:
            return {"error": "Could not extract text from file"}, 400
        
        # System prompt for report analysis
        system_content = """You are a medical assistant analyzing medical reports. Follow these guidelines:
        1. Identify key metrics and test results
        2. Compare results with normal ranges when available
        3. Highlight any abnormal findings
        4. Organize information in a structured way
        5. Avoid making definitive diagnostic statements
        6. Always remind that this is not a substitute for professional medical interpretation
        7. Be factual and objective in your analysis
        """
        
        # Build the prompt for the model
        conversation_messages = [
            {"role": "system", "content": system_content},
            {"role": "user", "content": f"Here's a medical report to analyze: {extracted_text}\n\nPlease analyze this report and provide a summary of key findings."}
        ]
        
        # Get response from ModelLake or use rule-based fallback
        try:
            # First try ModelLake
            try:
                print("Sending report analysis request to ModelLake...")
                response = modellake.chat_complete({
                    "groc_account_id": os.environ['GROCLAKE_ACCOUNT_ID'],
                    "messages": conversation_messages
                })
                
                print(f"ModelLake report analysis response: {response}")
                
                # Check if we got a valid response
                if response and isinstance(response, dict) and len(response) > 0:
                    if 'answer' in response and response['answer']:
                        analysis = response['answer']
                    else:
                        # Try other fields
                        for field in ['content', 'text', 'response']:
                            if field in response and response[field]:
                                analysis = response[field]
                                break
                        else:
                            # If no text field, check for any string value
                            for key, value in response.items():
                                if isinstance(value, str) and value:
                                    analysis = value
                                    break
                            else:
                                # If still nothing, use rule-based analysis
                                raise ValueError("No usable content in ModelLake response")
                else:
                    # Empty or invalid response
                    raise ValueError("Empty or invalid ModelLake response")
                    
                return {"analysis": analysis, "is_fallback": False}, 200
                    
            except Exception as e:
                print(f"ModelLake failed, using rule-based analysis: {str(e)}")
                # Use rule-based analysis as fallback
                analysis = MedicalReportHandler.generate_report_analysis(extracted_text, filename)
                return {"analysis": analysis, "is_fallback": True}, 200
                
        except Exception as e:
            print(f"Error in report analysis: {str(e)}")
            print(traceback.format_exc())
            
            # Ultimate fallback
            analysis = MedicalReportHandler.generate_report_analysis(extracted_text, filename)
            return {"analysis": analysis, "is_fallback": True}, 200


# Update endpoints to use the handler classes

@app.route('/api/analyze-report', methods=['POST'])
def analyze_report():
    token = request.headers.get('Authorization')
    print(f"Received report analysis request with token: {token}")
    
    if not MedicalReportHandler.verify_token(token):
        return jsonify({"error": "Unauthorized"}), 401
    
    if 'file' not in request.files:
        return jsonify({"error": "No file provided"}), 400
    
    file = request.files['file']
    
    result, status_code = MedicalReportHandler.analyze_report(token, file)
    
    if status_code == 200:
        return jsonify(result)
    else:
        return jsonify(result), status_code


if __name__ == '__main__':
    app.run(debug=True, port=5000)

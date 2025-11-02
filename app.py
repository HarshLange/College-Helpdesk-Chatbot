from flask import Flask, render_template, request, jsonify, url_for, session
import google.generativeai as genai
from dotenv import load_dotenv
import os, re, json

# Load API Key
load_dotenv()
genai.configure(api_key=os.getenv("GEMINI_API_KEY"))

app = Flask(__name__)
# IMPORTANT: Set a secret key for session management
app.secret_key = os.getenv("FLASK_SECRET_KEY", "a_very_secret_key_that_should_be_changed")


# Load ARMIET knowledge base (JSON file)
try:
    with open("armiet_data.json", "r", encoding="utf-8") as f:
        armiet_data = json.load(f)
except FileNotFoundError:
    print("WARNING: armiet_data.json not found. Using empty knowledge base.")
    armiet_data = {}
except json.JSONDecodeError:
    print("ERROR: armiet_data.json is not valid JSON. Using empty knowledge base.")
    armiet_data = {}


model = genai.GenerativeModel("gemini-2.5-flash") 

def format_reply(text):
    """
    Converts markdown-like text (bold, lists) into clean HTML by
    processing line-by-line.
    Supports:
    - **bold** -> <b>bold</b>
    - Numbered lists (1. item, 2. item) -> <ol><li>item</li></ol>
    - Bulleted lists (*, -, • item) -> <ul><li>item</li></ul>
    - Headings (bold lines) and newlines -> <br>
    """
    
    # --- FIX FOR STRAY BULLETS ---
    text = re.sub(r'^\s*([*•-])\s*$\n', r'\1 ', text, flags=re.MULTILINE)
    text = re.sub(r'^\s*([0-9]+\.)\s*$\n', r'\1 ', text, flags=re.MULTILINE)
    
    text = re.sub(r'^\s*[*•-]\s*(\*\*[^\n]+\*\*)\s*$', r'\1', text, flags=re.MULTILINE)
    # --- END OF FIX ---


    # 1. Handle bold text first, across the entire block
    text = re.sub(r'\*\*(.*?)\*\*', r'<b>\1</b>', text)

    lines = text.strip().split('\n')
    html_output = []
    in_ol = False  # Tracks if we are currently inside an <ol>
    in_ul = False  # Tracks if we are currently inside a <ul>

    for line in lines:
        stripped_line = line.strip()

        # Check for numbered list item
        ol_match = re.match(r'^[0-9]+\.\s+(.*)', stripped_line)
        # Check for bulleted list item
        ul_match = re.match(r'^[*•-]\s+(.*)', stripped_line)

        if ol_match:
            item_text = ol_match.group(1)
            # Check if this item is just a bolded heading (which it shouldn't be)
            if re.match(r'^<b>.*</b>$', item_text):
                 if in_ol: html_output.append('</ol>'); in_ol = False
                 if in_ul: html_output.append('</ul>'); in_ul = False
                 html_output.append(f'{item_text}<br>')
                 continue

            if in_ul:  # Close previous ul if open
                html_output.append('</ul>')
                in_ul = False
            if not in_ol:  # Start new ol if not already started
                html_output.append('<ol>')
                in_ol = True
            html_output.append(f'<li>{item_text}</li>') # Add list item

        elif ul_match:
            item_text = ul_match.group(1)
            # Check if this item is just a bolded heading
            if re.match(r'^<b>.*</b>$', item_text):
                 if in_ol: html_output.append('</ol>'); in_ol = False
                 if in_ul: html_output.append('</ul>'); in_ul = False
                 html_output.append(f'{item_text}<br>')
                 continue
                 
            if in_ol:  # Close previous ol if open
                html_output.append('</ol>')
                in_ol = False
            if not in_ul:  # Start new ul if not already started
                html_output.append('<ul>')
                in_ul = True
            html_output.append(f'<li>{item_text}</li>') # Add list item

        else:  # Not a list item (plain text or heading)
            if in_ol:  # Close previous ol
                html_output.append('</ol>')
                in_ol = False
            if in_ul:  # Close previous ul
                html_output.append('</ul>')
                in_ul = False
            
            if stripped_line:
                html_output.append(f'{stripped_line}<br>') 

    # End of loop, close any open lists
    if in_ol:
        html_output.append('</ol>')
    if in_ul:
        html_output.append('</ul>')

    final_html = ''.join(html_output)
    
    if final_html.endswith('<br>'):
        final_html = final_html[:-4]
    final_html = re.sub(r'(<br\s*/?>\s*){2,}', '<br>', final_html)
    final_html = re.sub(r'</(ol|ul)><br>', r'</\1>', final_html)
    final_html = re.sub(r'<br><(ol|ul)>', r'<\1>', final_html)

    return final_html.strip()


@app.route("/")
def index():
    # Initialize chat_open status in session
    session['chat_open'] = False
    return render_template("index.html")

# New route to update chat_open status
@app.route("/update_chat_status", methods=["POST"])
def update_chat_status():
    data = request.json
    session['chat_open'] = data.get('chat_open', False)
    return jsonify({"status": "success"})


@app.route("/chat", methods=["POST"])
def chat():
    user_input = request.json.get("message")

    try:
        knowledge = json.dumps(armiet_data, indent=2)

        prompt = f"""
You are a polite, student-friendly helpdesk assistant for ARMIET Educational Institute (Alamuri Ratnamala Institute of Engineering and Technology).
Your answers must be short, clear, and professional (2-3 sentences).
Always answer ONLY with ARMIET Educational Institute information from the Knowledge Base. If the answer isn't there, politely say you don't have that information.

---
**EXCEPTION: ALWAYS PROVIDE FULL LISTS**
If the user asks for a list (like documents, courses, or requirements), **you MUST provide the full list in a bulleted or numbered format.** Do not just summarize and say "documents are required." You must *actually list them out* (e.g., "Key documents include: \n * SSC Marksheet \n * HSC Marksheet").
This rule OVERRULES the "2-3 sentence" limit.
---

**IMPORTANT FORMATTING RULES:**
1.  **Emojis:** Use emojis for categories: 📘 for courses, 💰 for fees, 📑 for documents/admissions.
2.  **Bold:** **Bold** important names (like `**ARMIET Educational Institute**` or specific department names) using markdown's double asterisks.
3.  **Lists:**
    * Use numbered (1., 2.) or bulleted (*, -) lists for items. Put the bullet/number on the **SAME LINE** as the text.
    * If you have categories *within* a list (like 'Diploma' and 'Bachelor'), make the category name **bold** on its own line, but **DO NOT** put a bullet point (*) or number on it.

**Correct List Example:**
Here are the courses:
**Diploma in Engineering**
* Civil Engineering
* Mechanical Engineering
**Bachelor of Engineering**
* Computer Engineering

**Bad List Example (DO NOT DO THIS):**
•
**Diploma in Engineering**
•
Civil Engineering
---

Knowledge Base:
{knowledge}

Student: {user_input}
Helpdesk:
"""
        response = model.generate_content(prompt)
        reply = format_reply(response.text.strip())
        
        # Check if chat is open, if not, indicate an unread message
        unread_message = not session.get('chat_open', False)

        return jsonify({"reply": reply, "unread_message": unread_message})
    except Exception as e:
        print("Gemini API Error:", e)
        return jsonify({"reply": "⚠️ Sorry, something went wrong with the helpdesk bot.", "unread_message": True})

if __name__ == "__main__":
    app.run(debug=True)
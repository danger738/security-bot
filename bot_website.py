from flask import Flask, render_template
import threading
import os

app = Flask(__name__)

@app.route('/')
def home():
    return render_template('index.html')

def run_website():
    app.run(host='0.0.0.0', port=int(os.getenv('WEBSITE_PORT', '8080')))

def start_website():
    website_thread = threading.Thread(target=run_website)
    website_thread.daemon = True
    website_thread.start()

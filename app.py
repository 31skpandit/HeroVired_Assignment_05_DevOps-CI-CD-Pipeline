from flask import Flask, render_template, request, redirect, url_for
from flask_pymongo import PyMongo
from bson.objectid import ObjectId
from dotenv import load_dotenv
from pymongo.errors import PyMongoError
import certifi
import os

# Load env vars
load_dotenv()

app = Flask(__name__)
app.config["MONGO_URI"] = os.getenv("MONGO_URI")
app.secret_key = os.getenv("SECRET_KEY")

# Use certifi CA bundle explicitly for cross-platform TLS reliability with
# MongoDB Atlas (notably fixes common macOS certificate verification failures).
# Only applied for mongodb+srv:// (Atlas) URIs -- a plain mongodb:// URI (e.g.
# a local/test MongoDB with no TLS) must not be forced into a TLS handshake,
# or the driver fails with "SSL handshake failed" against a non-TLS server.
_mongo_kwargs = {}
if (app.config["MONGO_URI"] or "").startswith("mongodb+srv://"):
    _mongo_kwargs["tlsCAFile"] = certifi.where()
mongo = PyMongo(app, **_mongo_kwargs)

# Home page -> list students
@app.route('/')
def index():
    students = mongo.db.students.find()
    return render_template('index.html', students=students)

# Add student
@app.route('/add', methods=['GET', 'POST'])
def add_student():
    if request.method == 'POST':
        name = request.form['name']
        email = request.form['email']
        course = request.form['course']
        mongo.db.students.insert_one({
            "name": name,
            "email": email,
            "course": course
        })
        return redirect(url_for('index'))
    return render_template('add_student.html')

# Update student
@app.route('/update/<student_id>', methods=['GET', 'POST'])
def update_student(student_id):
    student = mongo.db.students.find_one({"_id": ObjectId(student_id)})
    if request.method == 'POST':
        new_name = request.form['name']
        new_email = request.form['email']
        new_course = request.form['course']
        mongo.db.students.update_one(
            {"_id": ObjectId(student_id)},
            {"$set": {"name": new_name, "email": new_email, "course": new_course}}
        )
        return redirect(url_for('index'))
    return render_template('update_student.html', student=student)


# Delete student
@app.route('/delete/<student_id>')
def delete_student(student_id):
    mongo.db.students.delete_one({"_id": ObjectId(student_id)})
    return redirect(url_for('index'))

# Health check -> used by the CI/CD pipeline as the deploy-verification gate
@app.route('/health')
def health():
    try:
        mongo.cx.admin.command('ping')
        return {"status": "ok", "mongo": "connected"}, 200
    except PyMongoError as e:
        return {"status": "error", "mongo": str(e)}, 503

if __name__ == '__main__':
    app.run(host="0.0.0.0", debug=True, port=5000)

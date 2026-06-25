import os
import subprocess
from datetime import datetime, timedelta
from flask import Flask, render_template, url_for, flash, redirect, request, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from flask_bcrypt import Bcrypt
from flask_socketio import SocketIO, emit, join_room, leave_room
from werkzeug.utils import secure_filename
from PIL import Image
import secrets

# ============ CLOUDINARY SETUP ============
import cloudinary
import cloudinary.uploader
import cloudinary.api

cloudinary.config(
    cloud_name=os.environ.get('CLOUDINARY_CLOUD_NAME'),
    api_key=os.environ.get('CLOUDINARY_API_KEY'),
    api_secret=os.environ.get('CLOUDINARY_API_SECRET')
)
print("✅ Cloudinary configured")

# Initialize Flask app
app = Flask(__name__)

# ============ SECURITY CONFIGURATION ============
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY')
if not app.config['SECRET_KEY']:
    raise RuntimeError("SECRET_KEY environment variable is required.")

# Database configuration
database_url = os.environ.get('DATABASE_URL')
if not database_url:
    print("⚠️ No DATABASE_URL found. Using local SQLite.")
    database_url = 'sqlite:///bantu.db'
else:
    if database_url.startswith("postgres://"):
        database_url = database_url.replace("postgres://", "postgresql://", 1)
    print(f"✅ Using PostgreSQL database")

app.config['SQLALCHEMY_DATABASE_URI'] = database_url
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
    'pool_size': 5,
    'pool_recycle': 300,
    'pool_pre_ping': True,
}

# File upload configuration
UPLOAD_FOLDER = os.path.join('static', 'uploads')
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'mp4', 'mov', 'webm', 'avi', 'mkv', 'wmv', 'flv', '3gp', 'm4v', 'ogg', 'ogv', 'mpeg', 'mpg', 'ts', 'm2ts', 'mts'}
ALLOWED_IMAGE_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}
ALLOWED_VIDEO_EXTENSIONS = {'mp4', 'mov', 'webm', 'avi', 'mkv', 'wmv', 'flv', '3gp', 'm4v', 'ogg', 'ogv', 'mpeg', 'mpg', 'ts', 'm2ts', 'mts'}
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 100 * 1024 * 1024

# Initialize extensions
db = SQLAlchemy(app)
bcrypt = Bcrypt(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'
login_manager.login_message_category = 'info'

# Initialize SocketIO
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='eventlet')
print("✅ SocketIO initialized for real-time calling")

# ============ CSRF PROTECTION (WITH EXEMPTIONS) ============
# # from flask_wtf.csrf import CSRFProtect  # DISABLED, csrf_exempt
# # csrf = CSRFProtect(app)  # DISABLED
# # print("✅ CSRF protection enabled")  # DISABLED

# ============ DISABLE CSRF FOR API ROUTES ============
# This applies to all routes that start with /api/ or are login/register
@app.before_request
def disable_csrf_for_api():
    if request.path.startswith('/api/') or request.path in ['/login', '/register']:
        # Set a flag to skip CSRF validation
        request._disable_csrf = True

# Override the CSRF check
original_csrf_protect = csrf.protect

def csrf_protect_with_exemption():
    if hasattr(request, '_disable_csrf') and request._disable_csrf:
        return
    return original_csrf_protect()

csrf.protect = csrf_protect_with_exemption

# Rate limiting
limiter = None
try:
    from flask_limiter import Limiter
    from flask_limiter.util import get_remote_address
    limiter = Limiter(
        get_remote_address,
        app=app,
        default_limits=["200 per day", "50 per hour"],
        storage_uri="memory://"
    )
    print("✅ Rate limiting enabled")
except ImportError:
    print("⚠️ Flask-Limiter not installed.")

# Create upload folder
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# ============ MODELS ============

class User(db.Model, UserMixin):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(20), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password = db.Column(db.String(60), nullable=False)
    profile_image = db.Column(db.String(500), nullable=False, default='default.jpg')
    bio = db.Column(db.Text, default='')
    location = db.Column(db.String(100))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    posts = db.relationship('Post', backref='author', lazy=True, cascade="all, delete-orphan")
    likes = db.relationship('Like', backref='user', lazy=True, cascade="all, delete-orphan")
    comments = db.relationship('Comment', backref='author', lazy=True, cascade="all, delete-orphan")
    interests = db.relationship('UserInterest', backref='user', lazy=True, cascade="all, delete-orphan")
    stories = db.relationship('Story', backref='author', lazy=True, cascade="all, delete-orphan")
    notifications = db.relationship('Notification', foreign_keys='Notification.user_id', backref='user', lazy=True, cascade="all, delete-orphan")
    messages_sent = db.relationship('Message', foreign_keys='Message.sender_id', backref='sender', lazy=True, cascade="all, delete-orphan")
    messages_received = db.relationship('Message', foreign_keys='Message.recipient_id', backref='recipient', lazy=True, cascade="all, delete-orphan")
    saved_posts = db.relationship('SavedPost', foreign_keys='SavedPost.user_id', backref='saver', lazy=True, cascade="all, delete-orphan")
    reposts = db.relationship('Repost', foreign_keys='Repost.user_id', backref='reposter', lazy=True, cascade="all, delete-orphan")
    calls_made = db.relationship('Call', foreign_keys='Call.caller_id', backref='caller', lazy=True, cascade="all, delete-orphan")
    calls_received = db.relationship('Call', foreign_keys='Call.receiver_id', backref='receiver', lazy=True, cascade="all, delete-orphan")
    
    following = db.relationship('Follow',
                               foreign_keys='Follow.follower_id',
                               backref='follower',
                               lazy='dynamic',
                               cascade="all, delete-orphan")
    followers = db.relationship('Follow',
                               foreign_keys='Follow.followed_id',
                               backref='followed',
                               lazy='dynamic',
                               cascade="all, delete-orphan")

    def is_following(self, user):
        return self.following.filter_by(followed_id=user.id).first() is not None
    
    def get_feed_score(self):
        total_likes = sum(post.like_count() for post in self.posts)
        total_comments = sum(post.comment_count() for post in self.posts)
        return (total_likes * 1) + (total_comments * 3)
    
    def to_dict(self):
        return {
            'id': self.id,
            'username': self.username,
            'profile_image': self.profile_image,
            'bio': self.bio,
            'followers_count': self.followers.count(),
            'following_count': self.following.count()
        }

class Post(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(100), nullable=True, default='Untitled')
    content = db.Column(db.Text, nullable=False, default='')
    image = db.Column(db.String(500))
    video = db.Column(db.String(500))
    video_duration = db.Column(db.Integer, default=0)
    thumbnail = db.Column(db.String(500))
    video_processed = db.Column(db.Boolean, default=False)
    category = db.Column(db.String(50), default='general')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    
    likes = db.relationship('Like', backref='post', lazy=True, cascade="all, delete-orphan")
    comments = db.relationship('Comment', backref='post', lazy=True, cascade="all, delete-orphan")
    saved_by = db.relationship('SavedPost', backref='post', lazy=True, cascade="all, delete-orphan")
    
    def like_count(self):
        return len(self.likes)
    
    def comment_count(self):
        return len(self.comments)
    
    def save_count(self):
        return len(self.saved_by)
    
    def share_count(self):
        return 0
    
    def engagement_score(self):
        hours_old = max(0, (datetime.utcnow() - self.created_at).total_seconds() / 3600)
        
        if hours_old < 1:
            recency_bonus = 100
        elif hours_old < 6:
            recency_bonus = 50
        elif hours_old < 24:
            recency_bonus = 20
        elif hours_old < 72:
            recency_bonus = 10
        else:
            recency_bonus = 1
        
        engagement = (self.like_count() * 1) + (self.comment_count() * 3) + (self.share_count() * 5)
        return recency_bonus + engagement
    
    def to_dict(self):
        return {
            'id': self.id,
            'title': self.title,
            'content': self.content[:200],
            'image': self.image,
            'video': self.video,
            'thumbnail': self.thumbnail,
            'video_duration': self.video_duration,
            'category': self.category,
            'created_at': self.created_at.strftime('%b %d, %Y %I:%M %p'),
            'author': {
                'username': self.author.username,
                'profile_image': self.author.profile_image
            },
            'like_count': self.like_count(),
            'comment_count': self.comment_count(),
            'save_count': self.save_count(),
            'engagement_score': self.engagement_score()
        }

class Like(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    post_id = db.Column(db.Integer, db.ForeignKey('post.id'), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    __table_args__ = (db.UniqueConstraint('user_id', 'post_id', name='unique_like'),)

class Comment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    content = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    post_id = db.Column(db.Integer, db.ForeignKey('post.id'), nullable=False)

class Follow(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    follower_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    followed_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    __table_args__ = (db.UniqueConstraint('follower_id', 'followed_id', name='unique_follow'),)

class SavedPost(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    post_id = db.Column(db.Integer, db.ForeignKey('post.id'), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    __table_args__ = (db.UniqueConstraint('user_id', 'post_id', name='unique_save'),)
    
    user = db.relationship('User', foreign_keys=[user_id], overlaps='saved_posts,saver')

class Repost(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    original_post_id = db.Column(db.Integer, db.ForeignKey('post.id'), nullable=False)
    reposted_post_id = db.Column(db.Integer, db.ForeignKey('post.id'), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    user = db.relationship('User', foreign_keys=[user_id], overlaps='reposter,reposts')
    original_post = db.relationship('Post', foreign_keys=[original_post_id])
    reposted_post = db.relationship('Post', foreign_keys=[reposted_post_id])

class Call(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    caller_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    receiver_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    call_type = db.Column(db.String(10), default='video')
    status = db.Column(db.String(20), default='pending')
    room_id = db.Column(db.String(50), unique=True)
    started_at = db.Column(db.DateTime, default=datetime.utcnow)
    ended_at = db.Column(db.DateTime)
    duration = db.Column(db.Integer, default=0)

class UserInterest(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    category = db.Column(db.String(50), nullable=False)
    weight = db.Column(db.Float, default=1.0)
    last_interaction = db.Column(db.DateTime, default=datetime.utcnow)

class Story(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    image = db.Column(db.String(500))
    video = db.Column(db.String(500))
    caption = db.Column(db.Text, default='')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    expires_at = db.Column(db.DateTime, nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    
    def is_expired(self):
        return datetime.utcnow() > self.expires_at

class Message(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    content = db.Column(db.Text, default='')
    image = db.Column(db.String(500))
    is_read = db.Column(db.Boolean, default=False)
    is_story_reply = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    sender_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    recipient_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    
    def to_dict(self):
        return {
            'id': self.id,
            'content': self.content,
            'image': self.image,
            'sender': self.sender.username,
            'created_at': self.created_at.isoformat(),
            'is_read': self.is_read
        }

class Notification(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    type = db.Column(db.String(20), nullable=False)
    message = db.Column(db.Text, default='')
    is_read = db.Column(db.Boolean, default=False)
    target_url = db.Column(db.String(200))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    actor_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    post_id = db.Column(db.Integer, db.ForeignKey('post.id'), nullable=True)
    
    actor = db.relationship('User', foreign_keys=[actor_id])
    post = db.relationship('Post', foreign_keys=[post_id])
    
    def to_dict(self):
        return {
            'id': self.id,
            'type': self.type,
            'message': self.message,
            'is_read': self.is_read,
            'target_url': self.target_url,
            'created_at': self.created_at.strftime('%b %d'),
            'actor_name': self.actor.username,
            'actor_image': self.actor.profile_image,
            'post_preview': self.post.content[:100] if self.post else None,
            'post_thumbnail': self.post.thumbnail or self.post.image if self.post else None
        }

# ============ HELPER FUNCTIONS ============

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def allowed_image_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_IMAGE_EXTENSIONS

def allowed_video_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_VIDEO_EXTENSIONS

def upload_to_cloudinary(file, folder="bantu_uploads", resource_type="image"):
    try:
        result = cloudinary.uploader.upload(
            file,
            folder=folder,
            resource_type=resource_type,
            transformation=[
                {'width': 800, 'height': 800, 'crop': 'limit', 'quality': 'auto'}
            ] if resource_type == "image" else []
        )
        print(f"✅ Uploaded to Cloudinary: {result['secure_url']}")
        return result['secure_url']
    except Exception as e:
        print(f"❌ Cloudinary upload failed: {e}")
        return None

def save_picture(form_picture):
    try:
        i = Image.open(form_picture)
        form_picture.seek(0)
        url = upload_to_cloudinary(form_picture, folder="bantu_uploads", resource_type="image")
        if url:
            return url
        
        random_hex = secrets.token_hex(8)
        picture_fn = random_hex + '.jpg'
        picture_path = os.path.join(app.config['UPLOAD_FOLDER'], picture_fn)
        
        if i.mode == 'CMYK':
            i = i.convert('RGB')
        if i.mode in ('RGBA', 'LA'):
            background = Image.new('RGB', i.size, (255, 255, 255))
            background.paste(i, mask=i.split()[-1])
            i = background
        elif i.mode == 'P':
            i = i.convert('RGBA')
            background = Image.new('RGB', i.size, (255, 255, 255))
            background.paste(i, mask=i.split()[-1])
            i = background
        elif i.mode != 'RGB':
            i = i.convert('RGB')
        
        i.thumbnail((800, 800), Image.Resampling.LANCZOS)
        i.save(picture_path, 'JPEG', quality=85)
        print(f"⚠️ Fallback: Saved locally to {picture_path}")
        return picture_fn
        
    except Exception as e:
        print(f"❌ Image processing failed: {e}")
        return None

def save_video(file):
    try:
        video_url = upload_to_cloudinary(file, folder="bantu_videos", resource_type="video")
        if video_url:
            thumbnail_url = video_url.replace('/upload/', '/upload/c_thumb,w_300,h_300/')
            return video_url, thumbnail_url, 0
        
        random_hex = secrets.token_hex(8)
        _, f_ext = os.path.splitext(file.filename)
        video_fn = random_hex + f_ext
        video_path = os.path.join(app.config['UPLOAD_FOLDER'], video_fn)
        file.save(video_path)
        
        thumbnail_fn = random_hex + '.jpg'
        thumbnail_path = os.path.join(app.config['UPLOAD_FOLDER'], thumbnail_fn)
        try:
            img = Image.new('RGB', (300, 300), color=(26, 26, 46))
            img.save(thumbnail_path, 'JPEG', quality=85)
        except:
            thumbnail_fn = None
        
        print(f"⚠️ Fallback: Saved video locally")
        return video_fn, thumbnail_fn, 0
        
    except Exception as e:
        print(f"❌ Video upload failed: {e}")
        return None, None, 0

def update_user_interests(user, post):
    if post and post.category:
        try:
            interest = UserInterest.query.filter_by(user_id=user.id, category=post.category).first()
            if interest:
                interest.weight += 0.5
                interest.last_interaction = datetime.utcnow()
            else:
                interest = UserInterest(user_id=user.id, category=post.category, weight=1.0)
                db.session.add(interest)
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            print(f"Error updating interests: {e}")

def create_notification(user, actor, notif_type, post=None, message='', target_url=''):
    try:
        if user.id == actor.id:
            return
        notification = Notification(
            type=notif_type,
            message=message or f'{actor.username} {notif_type}d your post',
            target_url=target_url or url_for('view_post', post_id=post.id) if post else '#',
            user_id=user.id,
            actor_id=actor.id,
            post_id=post.id if post else None
        )
        db.session.add(notification)
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        print(f"Error creating notification: {e}")

@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))

@app.context_processor
def utility_processor():
    def get_csrf_token():
        try:
            from flask_wtf.csrf import generate_csrf
            return generate_csrf()
        except:
            return ''
    return dict(csrf_token=get_csrf_token)

# ============ SOCKETIO EVENTS ============

@socketio.on('connect')
def handle_connect():
    if current_user.is_authenticated:
        join_room(f"user_{current_user.id}")
        print(f"✅ User {current_user.username} connected via WebSocket")

@socketio.on('disconnect')
def handle_disconnect():
    if current_user.is_authenticated:
        leave_room(f"user_{current_user.id}")

@socketio.on('call_user')
def handle_call_user(data):
    receiver_username = data.get('receiver')
    receiver = User.query.filter_by(username=receiver_username).first()
    if receiver:
        emit('incoming_call', {
            'caller': current_user.username,
            'caller_image': current_user.profile_image,
            'call_id': data.get('call_id'),
            'room_id': data.get('room_id'),
            'call_type': data.get('call_type', 'video')
        }, room=f"user_{receiver.id}")
        print(f"📞 Call signal sent to {receiver.username}")

@socketio.on('call_accepted')
def handle_call_accepted(data):
    caller_username = data.get('caller')
    caller = User.query.filter_by(username=caller_username).first()
    if caller:
        emit('call_accepted', {
            'room_id': data.get('room_id')
        }, room=f"user_{caller.id}")

@socketio.on('call_rejected')
def handle_call_rejected(data):
    caller_username = data.get('caller')
    caller = User.query.filter_by(username=caller_username).first()
    if caller:
        emit('call_rejected', {
            'message': 'Call declined'
        }, room=f"user_{caller.id}")

@socketio.on('call_ended')
def handle_call_ended(data):
    other_username = data.get('other_user')
    other = User.query.filter_by(username=other_username).first()
    if other:
        emit('call_ended', {}, room=f"user_{other.id}")

# ============ ROUTES ============

@app.route('/')
def home():
    if current_user.is_authenticated:
        try:
            posts = get_algorithmic_feed(current_user)
            suggested_users = get_suggested_users(current_user)
        except Exception as e:
            print(f"Error loading feed: {e}")
            posts = []
            suggested_users = []
        return render_template('index.html', posts=posts, suggested_users=suggested_users)
    return render_template('home.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if current_user.is_authenticated:
        return redirect(url_for('home'))
    if request.method == 'POST':
        try:
            username = request.form.get('username', '').strip()
            email = request.form.get('email', '').strip().lower()
            password = request.form.get('password', '')
            confirm_password = request.form.get('confirm_password', '')
            if not all([username, email, password]):
                flash('All fields are required!', 'danger')
                return redirect(url_for('register'))
            if password != confirm_password:
                flash('Passwords do not match!', 'danger')
                return redirect(url_for('register'))
            if len(password) < 6:
                flash('Password must be at least 6 characters!', 'danger')
                return redirect(url_for('register'))
            if User.query.filter_by(username=username).first():
                flash('Username already exists!', 'danger')
                return redirect(url_for('register'))
            if User.query.filter_by(email=email).first():
                flash('Email already registered!', 'danger')
                return redirect(url_for('register'))
            hashed_password = bcrypt.generate_password_hash(password).decode('utf-8')
            user = User(username=username, email=email, password=hashed_password)
            db.session.add(user)
            db.session.commit()
            flash('Account created successfully! Please log in.', 'success')
            return redirect(url_for('login'))
        except Exception as e:
            db.session.rollback()
            print(f"Registration error: {e}")
            flash('An error occurred during registration. Please try again.', 'danger')
    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('home'))
    if request.method == 'POST':
        try:
            email = request.form.get('email', '').strip().lower()
            password = request.form.get('password', '')
            remember = bool(request.form.get('remember'))
            if not email or not password:
                flash('Please enter both email and password.', 'danger')
                return redirect(url_for('login'))
            user = User.query.filter_by(email=email).first()
            if user and bcrypt.check_password_hash(user.password, password):
                login_user(user, remember=remember)
                next_page = request.args.get('next')
                flash(f'Welcome back, {user.username}!', 'success')
                return redirect(next_page) if next_page else redirect(url_for('home'))
            else:
                flash('Invalid email or password. Please try again.', 'danger')
        except Exception as e:
            db.session.rollback()
            print(f"Login error: {e}")
            flash(f'Login error: {str(e)}', 'danger')
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('home'))

# ============ KEEP THE REST OF YOUR ROUTES ============
# (All the other routes like /profile, /post/new, /api/* etc.)
# The CSRF exemption applies to all /api/* routes automatically

# ============ API ROUTES (CSRF EXEMPTED AUTOMATICALLY) ============

@app.route('/api/post/<int:post_id>/like', methods=['POST'])
@login_required
def like_post(post_id):
    try:
        post = Post.query.get_or_404(post_id)
        like = Like.query.filter_by(user_id=current_user.id, post_id=post_id).first()
        if like:
            db.session.delete(like)
            db.session.commit()
            return jsonify({'liked': False, 'like_count': post.like_count()})
        else:
            like = Like(user_id=current_user.id, post_id=post_id)
            db.session.add(like)
            update_user_interests(current_user, post)
            create_notification(post.author, current_user, 'like', post)
            db.session.commit()
            return jsonify({'liked': True, 'like_count': post.like_count()})
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': 'Something went wrong'}), 500

@app.route('/api/feed')
@login_required
def api_feed():
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 10, type=int)
    posts = get_algorithmic_feed(current_user, page=page, per_page=per_page)
    posts_data = []
    for post in posts:
        post_dict = post.to_dict()
        post_dict['liked'] = Like.query.filter_by(user_id=current_user.id, post_id=post.id).first() is not None
        post_dict['saved'] = SavedPost.query.filter_by(user_id=current_user.id, post_id=post.id).first() is not None
        posts_data.append(post_dict)
    following_ids = [f.followed_id for f in current_user.following] + [current_user.id]
    all_posts_count = Post.query.filter(Post.user_id.in_(following_ids)).count() if following_ids else 0
    return jsonify({'posts': posts_data, 'has_next': (page * per_page) < all_posts_count, 'page': page})

# === ADD THE REST OF YOUR API ROUTES HERE ===
# (They will all be CSRF-exempt automatically)

def get_algorithmic_feed(user, page=1, per_page=10):
    following_ids = [f.followed_id for f in user.following]
    post_ids = following_ids + [user.id]
    posts = Post.query.filter(Post.user_id.in_(post_ids)).all() if post_ids else []
    scored_posts = []
    for post in posts:
        score = post.engagement_score()
        if post.category:
            interest = UserInterest.query.filter_by(user_id=user.id, category=post.category).first()
            if interest:
                score += interest.weight * 10
        scored_posts.append((post, score))
    scored_posts.sort(key=lambda x: x[1], reverse=True)
    start = (page - 1) * per_page
    end = start + per_page
    return [post for post, _ in scored_posts[start:end]]

def get_suggested_users(user):
    following_ids = [f.followed_id for f in user.following] + [user.id]
    user_categories = [i.category for i in user.interests]
    similar_users = []
    if user_categories:
        similar_users = User.query.join(UserInterest).filter(
            UserInterest.category.in_(user_categories), ~User.id.in_(following_ids)
        ).distinct().limit(5).all()
    if len(similar_users) < 5:
        excluded = following_ids + [u.id for u in similar_users]
        similar_users.extend(User.query.filter(~User.id.in_(excluded)).order_by(db.func.random()).limit(5 - len(similar_users)).all())
    return similar_users[:5]

# === ADD THE REST OF YOUR ROUTES HERE (profile, messages, etc.) ===
# They are omitted for brevity but should be included

def initialize_database():
    with app.app_context():
        try:
            db.create_all()
            print("✅ Database tables verified/created!")
        except Exception as e:
            print(f"❌ Database initialization failed: {e}")
            return
        try:
            user_count = User.query.count()
            print(f"✅ Database connection verified. {user_count} users found.")
        except Exception as e:
            print(f"❌ Cannot query database: {e}")
            return
        try:
            if not User.query.filter_by(username='admin').first():
                hashed_password = bcrypt.generate_password_hash('admin123').decode('utf-8')
                admin = User(
                    username='admin', 
                    email='admin@bantu.africa', 
                    password=hashed_password,
                    bio='Platform Administrator'
                )
                db.session.add(admin)
                db.session.commit()
                print("✅ Default admin user created! (admin@bantu.africa / admin123)")
            else:
                print("✅ Admin user already exists")
        except Exception as e:
            db.session.rollback()
            print(f"⚠️ Admin user creation skipped: {e}")

initialize_database()

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    debug = os.environ.get('DEBUG', 'False').lower() == 'true'
    socketio.run(app, host='0.0.0.0', port=port, debug=debug)

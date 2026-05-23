import os
import subprocess
from datetime import datetime, timedelta
from flask import Flask, render_template, url_for, flash, redirect, request, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from flask_bcrypt import Bcrypt
from werkzeug.utils import secure_filename
from PIL import Image
import secrets

# Initialize Flask app
app = Flask(__name__)

# ============ SECURITY CONFIGURATION ============
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY')
if not app.config['SECRET_KEY']:
    raise RuntimeError("SECRET_KEY environment variable is required. Add it in Render Dashboard → Environment.")

# Database configuration
database_url = os.environ.get('DATABASE_URL', 'sqlite:///bantu.db')
if database_url and database_url.startswith("postgres://"):
    database_url = database_url.replace("postgres://", "postgresql://", 1)
app.config['SQLALCHEMY_DATABASE_URI'] = database_url
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
    'pool_size': 5,
    'pool_recycle': 300,
    'pool_pre_ping': True,
}

# File upload configuration
UPLOAD_FOLDER = os.path.join('static', 'uploads')
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'mp4', 'mov', 'webm'}
ALLOWED_IMAGE_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}
ALLOWED_VIDEO_EXTENSIONS = {'mp4', 'mov', 'webm'}
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 100 * 1024 * 1024  # 100MB for video support

# Initialize extensions
db = SQLAlchemy(app)
bcrypt = Bcrypt(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'
login_manager.login_message_category = 'info'

# CSRF protection
csrf = None
try:
    from flask_wtf.csrf import CSRFProtect
    csrf = CSRFProtect(app)
    print("✅ CSRF protection enabled")
except ImportError:
    print("⚠️ Flask-WTF not installed. CSRF protection disabled.")

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
    print("⚠️ Flask-Limiter not installed. Rate limiting disabled.")

# Create upload folder and default image
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
default_image_path = os.path.join(UPLOAD_FOLDER, 'default.jpg')
if not os.path.exists(default_image_path):
    try:
        img = Image.new('RGB', (200, 200), color='#1a1a2e')
        img.save(default_image_path)
        print("✅ Default profile image created")
    except Exception as e:
        print(f"⚠️ Could not create default image: {e}")

# ============ MODELS ============

class User(db.Model, UserMixin):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(20), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password = db.Column(db.String(60), nullable=False)
    profile_image = db.Column(db.String(20), nullable=False, default='default.jpg')
    bio = db.Column(db.Text, default='')
    location = db.Column(db.String(100))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    posts = db.relationship('Post', backref='author', lazy=True, cascade="all, delete-orphan")
    likes = db.relationship('Like', backref='user', lazy=True, cascade="all, delete-orphan")
    comments = db.relationship('Comment', backref='author', lazy=True, cascade="all, delete-orphan")
    interests = db.relationship('UserInterest', backref='user', lazy=True, cascade="all, delete-orphan")
    stories = db.relationship('Story', backref='author', lazy=True, cascade="all, delete-orphan")
    notifications = db.relationship('Notification', backref='user', lazy=True, cascade="all, delete-orphan")
    messages_sent = db.relationship('Message', foreign_keys='Message.sender_id', backref='sender', lazy=True, cascade="all, delete-orphan")
    messages_received = db.relationship('Message', foreign_keys='Message.recipient_id', backref='recipient', lazy=True, cascade="all, delete-orphan")
    
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
    title = db.Column(db.String(100), nullable=False, default='Untitled')
    content = db.Column(db.Text, nullable=False, default='')
    image = db.Column(db.String(100))
    video = db.Column(db.String(100))
    video_duration = db.Column(db.Integer, default=0)
    thumbnail = db.Column(db.String(100))
    video_processed = db.Column(db.Boolean, default=False)
    category = db.Column(db.String(50), default='general')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    
    likes = db.relationship('Like', backref='post', lazy=True, cascade="all, delete-orphan")
    comments = db.relationship('Comment', backref='post', lazy=True, cascade="all, delete-orphan")
    
    def like_count(self):
        return len(self.likes)
    
    def comment_count(self):
        return len(self.comments)
    
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

class UserInterest(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    category = db.Column(db.String(50), nullable=False)
    weight = db.Column(db.Float, default=1.0)
    last_interaction = db.Column(db.DateTime, default=datetime.utcnow)

class Story(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    image = db.Column(db.String(100))
    video = db.Column(db.String(100))
    caption = db.Column(db.Text, default='')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    expires_at = db.Column(db.DateTime, nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    
    def is_expired(self):
        return datetime.utcnow() > self.expires_at

class Message(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    content = db.Column(db.Text, default='')
    image = db.Column(db.String(100))
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

def save_picture(form_picture):
    random_hex = secrets.token_hex(8)
    _, f_ext = os.path.splitext(form_picture.filename)
    picture_fn = random_hex + f_ext
    picture_path = os.path.join(app.config['UPLOAD_FOLDER'], picture_fn)
    
    try:
        output_size = (500, 500)
        i = Image.open(form_picture)
        i.thumbnail(output_size)
        i.save(picture_path)
    except Exception as e:
        print(f"Error saving picture: {e}")
        form_picture.save(picture_path)
    
    return picture_fn

def save_video(file):
    random_hex = secrets.token_hex(8)
    _, f_ext = os.path.splitext(file.filename)
    video_fn = random_hex + f_ext
    video_path = os.path.join(app.config['UPLOAD_FOLDER'], video_fn)
    file.save(video_path)
    
    thumbnail_fn = random_hex + '.jpg'
    thumbnail_path = os.path.join(app.config['UPLOAD_FOLDER'], thumbnail_fn)
    
    thumbnail_created = False
    try:
        subprocess.run([
            'ffmpeg', '-i', video_path, '-ss', '00:00:01',
            '-vframes', '1', '-q:v', '2', thumbnail_path
        ], check=True, capture_output=True, timeout=30)
        thumbnail_created = True
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        pass
    
    if not thumbnail_created:
        try:
            Image.new('RGB', (300, 300), color='#1a1a2e').save(thumbnail_path)
        except Exception:
            thumbnail_fn = None
    
    duration = 0
    try:
        result = subprocess.run([
            'ffprobe', '-v', 'error', '-show_entries', 'format=duration',
            '-of', 'default=noprint_wrappers=1:nokey=1', video_path
        ], capture_output=True, text=True, timeout=10)
        duration = int(float(result.stdout.strip()))
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired, ValueError):
        pass
    
    return video_fn, thumbnail_fn, duration

def update_user_interests(user, post):
    if post and post.category:
        try:
            interest = UserInterest.query.filter_by(
                user_id=user.id, 
                category=post.category
            ).first()
            
            if interest:
                interest.weight += 0.5
                interest.last_interaction = datetime.utcnow()
            else:
                interest = UserInterest(
                    user_id=user.id,
                    category=post.category,
                    weight=1.0
                )
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

# ============ CONTEXT PROCESSOR (CSRF FIX) ============

@app.context_processor
def utility_processor():
    def get_csrf_token():
        if csrf:
            from flask_wtf.csrf import generate_csrf
            return generate_csrf()
        return ''
    return dict(csrf_token=get_csrf_token)

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
            
            user = User.query.filter_by(email=email).first()
            
            if user and bcrypt.check_password_hash(user.password, password):
                login_user(user, remember=remember)
                next_page = request.args.get('next')
                return redirect(next_page) if next_page else redirect(url_for('home'))
            else:
                flash('Login failed. Check email and password.', 'danger')
        except Exception as e:
            print(f"Login error: {e}")
            flash('An error occurred during login. Please try again.', 'danger')
    
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('home'))

@app.route('/profile')
@login_required
def profile():
    try:
        user_posts = Post.query.filter_by(user_id=current_user.id)\
                      .order_by(Post.created_at.desc()).all()
        followers_count = current_user.followers.count()
        following_count = current_user.following.count()
        stories_count = Story.query.filter(
            Story.user_id == current_user.id,
            Story.expires_at > datetime.utcnow()
        ).count()
    except Exception as e:
        print(f"Profile error: {e}")
        user_posts = []
        followers_count = 0
        following_count = 0
        stories_count = 0
    
    return render_template('profile.html', 
                         user=current_user, 
                         posts=user_posts,
                         followers_count=followers_count,
                         following_count=following_count,
                         stories_count=stories_count)

@app.route('/profile/<username>')
@login_required
def user_profile(username):
    try:
        user = User.query.filter_by(username=username).first_or_404()
        posts = Post.query.filter_by(user_id=user.id)\
                   .order_by(Post.created_at.desc()).all()
        is_following = current_user.is_following(user) if current_user != user else None
        followers_count = user.followers.count()
        following_count = user.following.count()
        stories_count = Story.query.filter(
            Story.user_id == user.id,
            Story.expires_at > datetime.utcnow()
        ).count()
    except Exception as e:
        print(f"User profile error: {e}")
        flash('User not found.', 'danger')
        return redirect(url_for('home'))
    
    return render_template('user_profile.html', 
                         user=user, 
                         posts=posts,
                         is_following=is_following,
                         followers_count=followers_count,
                         following_count=following_count,
                         stories_count=stories_count)

@app.route('/profile/<username>/followers')
@login_required
def user_followers(username):
    user = User.query.filter_by(username=username).first_or_404()
    followers = [f.follower for f in user.followers]
    return render_template('followers.html', user=user, followers=followers)

@app.route('/profile/<username>/following')
@login_required
def user_following(username):
    user = User.query.filter_by(username=username).first_or_404()
    following = [f.followed for f in user.following]
    return render_template('following.html', user=user, following=following)

@app.route('/profile/update', methods=['GET', 'POST'])
@login_required
def update_profile():
    if request.method == 'POST':
        try:
            username = request.form.get('username', '').strip()
            if username:
                existing = User.query.filter(User.username == username, User.id != current_user.id).first()
                if existing:
                    flash('Username already taken!', 'danger')
                    return redirect(url_for('update_profile'))
                current_user.username = username
            
            current_user.bio = request.form.get('bio', '').strip()
            current_user.location = request.form.get('location', '').strip()
            
            if 'profile_image' in request.files:
                file = request.files['profile_image']
                if file and file.filename and allowed_image_file(file.filename):
                    filename = save_picture(file)
                    current_user.profile_image = filename
            
            db.session.commit()
            flash('Profile updated successfully!', 'success')
            return redirect(url_for('profile'))
        except Exception as e:
            db.session.rollback()
            print(f"Profile update error: {e}")
            flash('An error occurred. Please try again.', 'danger')
    
    return render_template('update_profile.html')

@app.route('/post/new', methods=['GET', 'POST'])
@login_required
def new_post():
    if request.method == 'POST':
        try:
            content = request.form.get('content', '').strip()
            if not content:
                flash('Post content cannot be empty!', 'danger')
                return redirect(url_for('new_post'))
            
            post = Post(
                title=request.form.get('title', 'Untitled').strip() or 'Untitled',
                content=content,
                category=request.form.get('category', 'general'),
                author=current_user
            )
            
            if 'image' in request.files:
                file = request.files['image']
                if file and file.filename and allowed_image_file(file.filename):
                    filename = save_picture(file)
                    post.image = filename
            
            if 'video' in request.files:
                file = request.files['video']
                if file and file.filename and allowed_video_file(file.filename):
                    video_fn, thumbnail_fn, duration = save_video(file)
                    post.video = video_fn
                    post.thumbnail = thumbnail_fn
                    post.video_duration = duration
                    post.video_processed = True
            
            db.session.add(post)
            db.session.commit()
            flash('Your post has been created!', 'success')
            return redirect(url_for('home'))
        except Exception as e:
            db.session.rollback()
            print(f"Post creation error: {e}")
            flash('An error occurred while creating your post.', 'danger')
    
    return render_template('create_post.html')

@app.route('/post/<int:post_id>')
@login_required
def view_post(post_id):
    post = Post.query.get_or_404(post_id)
    try:
        update_user_interests(current_user, post)
    except Exception as e:
        print(f"Interest update error: {e}")
    return render_template('view_post.html', post=post)

@app.route('/post/<int:post_id>/delete')
@login_required
def delete_post(post_id):
    post = Post.query.get_or_404(post_id)
    if post.author != current_user:
        flash('You cannot delete this post!', 'danger')
        return redirect(url_for('home'))
    
    try:
        for file_attr in ['video', 'thumbnail', 'image']:
            filename = getattr(post, file_attr)
            if filename:
                filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
                if os.path.exists(filepath):
                    os.remove(filepath)
        
        db.session.delete(post)
        db.session.commit()
        flash('Post deleted!', 'success')
    except Exception as e:
        db.session.rollback()
        print(f"Post deletion error: {e}")
        flash('An error occurred while deleting the post.', 'danger')
    
    return redirect(url_for('home'))

# ============ API ENDPOINTS ============

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

@app.route('/api/post/<int:post_id>/comment', methods=['POST'])
@login_required
def add_comment(post_id):
    try:
        post = Post.query.get_or_404(post_id)
        data = request.get_json() or {}
        content = data.get('content', '').strip()
        
        if not content:
            return jsonify({'error': 'Comment cannot be empty'}), 400
        
        comment = Comment(content=content, author=current_user, post=post)
        db.session.add(comment)
        update_user_interests(current_user, post)
        create_notification(post.author, current_user, 'comment', post, f'commented: "{content[:50]}..."')
        db.session.commit()
        
        return jsonify({
            'success': True,
            'comment': {
                'content': comment.content,
                'author': comment.author.username,
                'author_image': url_for('static', filename=f'uploads/{comment.author.profile_image}'),
                'created_at': comment.created_at.strftime('%b %d, %Y')
            },
            'comment_count': post.comment_count()
        })
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': 'Something went wrong'}), 500

@app.route('/follow/<username>')
@login_required
def follow_user(username):
    try:
        user = User.query.filter_by(username=username).first_or_404()
        
        if current_user == user:
            flash('You cannot follow yourself!', 'danger')
            return redirect(url_for('user_profile', username=username))
        
        if current_user.is_following(user):
            flash(f'You are already following {username}!', 'info')
            return redirect(url_for('user_profile', username=username))
        
        follow = Follow(follower_id=current_user.id, followed_id=user.id)
        db.session.add(follow)
        create_notification(user, current_user, 'follow')
        db.session.commit()
        flash(f'You are now following {username}!', 'success')
    except Exception as e:
        db.session.rollback()
        flash('An error occurred.', 'danger')
    
    return redirect(url_for('user_profile', username=username))

@app.route('/unfollow/<username>')
@login_required
def unfollow_user(username):
    try:
        user = User.query.filter_by(username=username).first_or_404()
        follow = Follow.query.filter_by(follower_id=current_user.id, followed_id=user.id).first()
        if follow:
            db.session.delete(follow)
            db.session.commit()
            flash(f'You have unfollowed {username}!', 'success')
    except Exception as e:
        db.session.rollback()
        flash('An error occurred.', 'danger')
    
    return redirect(url_for('user_profile', username=username))

@app.route('/search')
@login_required
def search():
    query = request.args.get('q', '').strip()
    users = []
    posts = []
    if query:
        try:
            users = User.query.filter(
                User.username.contains(query) | User.bio.contains(query)
            ).all()
            posts = Post.query.filter(
                Post.content.contains(query) | Post.title.contains(query)
            ).all()
        except Exception as e:
            print(f"Search error: {e}")
    
    return render_template('search.html', query=query, users=users, posts=posts)

# ============ STORIES ROUTES ============

@app.route('/stories/<username>')
@login_required
def view_stories(username):
    user = User.query.filter_by(username=username).first_or_404()
    stories = Story.query.filter(
        Story.user_id == user.id,
        Story.expires_at > datetime.utcnow()
    ).order_by(Story.created_at.asc()).all()
    return render_template('stories.html', stories=stories, story_owner=user)

@app.route('/stories/create', methods=['GET', 'POST'])
@login_required
def create_story():
    if request.method == 'POST':
        try:
            story = Story(
                caption=request.form.get('caption', '').strip(),
                expires_at=datetime.utcnow() + timedelta(hours=24),
                author=current_user
            )
            
            if 'image' in request.files:
                file = request.files['image']
                if file and file.filename and allowed_image_file(file.filename):
                    filename = save_picture(file)
                    story.image = filename
            
            if 'video' in request.files:
                file = request.files['video']
                if file and file.filename and allowed_video_file(file.filename):
                    video_fn, _, _ = save_video(file)
                    story.video = video_fn
            
            if not story.image and not story.video:
                flash('Please upload an image or video for your story.', 'danger')
                return redirect(url_for('create_story'))
            
            db.session.add(story)
            db.session.commit()
            flash('Story created! It will expire in 24 hours.', 'success')
            return redirect(url_for('home'))
        except Exception as e:
            db.session.rollback()
            flash('An error occurred.', 'danger')
    
    return render_template('create_story.html')

# ============ MESSAGES ROUTES ============

@app.route('/messages')
@login_required
def messages():
    sent_to = db.session.query(Message.recipient_id).filter(Message.sender_id == current_user.id).distinct()
    received_from = db.session.query(Message.sender_id).filter(Message.recipient_id == current_user.id).distinct()
    
    partner_ids = set()
    for (pid,) in sent_to:
        partner_ids.add(pid)
    for (pid,) in received_from:
        partner_ids.add(pid)
    
    conversations = []
    for pid in partner_ids:
        partner = db.session.get(User, pid)
        if partner:
            last_msg = Message.query.filter(
                ((Message.sender_id == current_user.id) & (Message.recipient_id == pid)) |
                ((Message.sender_id == pid) & (Message.recipient_id == current_user.id))
            ).order_by(Message.created_at.desc()).first()
            
            unread_count = Message.query.filter(
                Message.sender_id == pid,
                Message.recipient_id == current_user.id,
                Message.is_read == False
            ).count()
            
            conversations.append({
                'username': partner.username,
                'profile_image': partner.profile_image,
                'last_message': last_msg.content[:50] if last_msg else '',
                'last_time': last_msg.created_at.strftime('%I:%M %p') if last_msg else '',
                'unread': unread_count > 0,
                'unread_count': unread_count,
                'verified': False
            })
    
    conversations.sort(key=lambda x: x.get('last_time', ''), reverse=True)
    return render_template('messages.html', conversations=conversations)

@app.route('/api/messages/<username>')
@login_required
def api_get_messages(username):
    partner = User.query.filter_by(username=username).first_or_404()
    
    messages = Message.query.filter(
        ((Message.sender_id == current_user.id) & (Message.recipient_id == partner.id)) |
        ((Message.sender_id == partner.id) & (Message.recipient_id == current_user.id))
    ).order_by(Message.created_at.asc()).all()
    
    Message.query.filter(
        Message.sender_id == partner.id,
        Message.recipient_id == current_user.id,
        Message.is_read == False
    ).update({'is_read': True})
    db.session.commit()
    
    return jsonify({'messages': [m.to_dict() for m in messages]})

@app.route('/api/messages/send', methods=['POST'])
@login_required
def api_send_message():
    data = request.get_json() or {}
    recipient_username = data.get('recipient')
    content = data.get('message', '').strip()
    is_story_reply = data.get('is_story_reply', False)
    
    if not recipient_username or not content:
        return jsonify({'error': 'Recipient and message required'}), 400
    
    recipient = User.query.filter_by(username=recipient_username).first()
    if not recipient:
        return jsonify({'error': 'User not found'}), 404
    
    try:
        message = Message(
            content=content,
            sender_id=current_user.id,
            recipient_id=recipient.id,
            is_story_reply=is_story_reply
        )
        db.session.add(message)
        db.session.commit()
        return jsonify(message.to_dict())
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

@app.route('/api/messages/send-image', methods=['POST'])
@login_required
def api_send_image():
    recipient_username = request.form.get('recipient')
    recipient = User.query.filter_by(username=recipient_username).first()
    if not recipient:
        return jsonify({'error': 'User not found'}), 404
    
    if 'image' not in request.files:
        return jsonify({'error': 'No image provided'}), 400
    
    file = request.files['image']
    if not file or not allowed_image_file(file.filename):
        return jsonify({'error': 'Invalid image'}), 400
    
    try:
        filename = save_picture(file)
        message = Message(
            content='📷 Image',
            image=filename,
            sender_id=current_user.id,
            recipient_id=recipient.id
        )
        db.session.add(message)
        db.session.commit()
        return jsonify(message.to_dict())
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

# ============ NOTIFICATIONS ROUTES ============

@app.route('/notifications')
@login_required
def notifications():
    notifications_list = Notification.query.filter_by(user_id=current_user.id)\
        .order_by(Notification.created_at.desc()).limit(50).all()
    formatted = [n.to_dict() for n in notifications_list]
    return render_template('notifications.html', notifications=formatted)

@app.route('/api/notifications/<int:notif_id>/read', methods=['POST'])
@login_required
def mark_notification_read(notif_id):
    notif = Notification.query.get_or_404(notif_id)
    if notif.user_id != current_user.id:
        return jsonify({'error': 'Unauthorized'}), 403
    notif.is_read = True
    db.session.commit()
    return jsonify({'success': True})

@app.route('/api/notifications/read-all', methods=['POST'])
@login_required
def mark_all_read():
    Notification.query.filter_by(user_id=current_user.id, is_read=False)\
        .update({'is_read': True})
    db.session.commit()
    return jsonify({'success': True})

@app.route('/api/notifications/unread-count')
@login_required
def unread_notification_count():
    count = Notification.query.filter_by(
        user_id=current_user.id, 
        is_read=False
    ).count()
    return jsonify({'count': count})

# ============ ALGORITHM FUNCTIONS ============

def get_algorithmic_feed(user, page=1, per_page=10):
    following_ids = [f.followed_id for f in user.following]
    post_ids = following_ids + [user.id]
    posts = Post.query.filter(Post.user_id.in_(post_ids)).all() if post_ids else []
    
    scored_posts = []
    for post in posts:
        score = post.engagement_score()
        if post.category:
            interest = UserInterest.query.filter_by(
                user_id=user.id, 
                category=post.category
            ).first()
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
            UserInterest.category.in_(user_categories),
            ~User.id.in_(following_ids)
        ).distinct().limit(5).all()
    
    if len(similar_users) < 5:
        excluded = following_ids + [u.id for u in similar_users]
        popular_users = User.query.filter(
            ~User.id.in_(excluded)
        ).order_by(db.func.random()).limit(5 - len(similar_users)).all()
        similar_users.extend(popular_users)
    
    return similar_users[:5]

# ============ API FEED ENDPOINTS ============

@app.route('/api/feed')
@login_required
def api_feed():
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 10, type=int)
    posts = get_algorithmic_feed(current_user, page=page, per_page=per_page)
    
    posts_data = []
    for post in posts:
        post_dict = post.to_dict()
        post_dict['liked'] = Like.query.filter_by(
            user_id=current_user.id, post_id=post.id
        ).first() is not None
        posts_data.append(post_dict)
    
    following_ids = [f.followed_id for f in current_user.following] + [current_user.id]
    all_posts_count = Post.query.filter(Post.user_id.in_(following_ids)).count() if following_ids else 0
    
    return jsonify({
        'posts': posts_data,
        'has_next': (page * per_page) < all_posts_count,
        'page': page
    })

@app.route('/api/trending')
def api_trending():
    one_day_ago = datetime.utcnow() - timedelta(days=1)
    posts = Post.query.filter(Post.created_at >= one_day_ago).all()
    scored_posts = [(post, post.engagement_score()) for post in posts]
    scored_posts.sort(key=lambda x: x[1], reverse=True)
    trending = [{'id': p.id, 'title': p.title, 'engagement_score': s} for p, s in scored_posts[:10]]
    return jsonify({'trending': trending})

# ============ INITIALIZATION ============

def initialize_database():
    with app.app_context():
        try:
            db.create_all()
            print("✅ Database tables verified/created!")
        except Exception as e:
            print(f"⚠️ Database initialization warning: {e}")
        
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
                print("✅ Default admin user created!")
        except Exception as e:
            db.session.rollback()
            print(f"⚠️ Admin user creation skipped: {e}")

initialize_database()

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    debug = os.environ.get('DEBUG', 'False').lower() == 'true'
    app.run(host='0.0.0.0', port=port, debug=debug)

import os
from datetime import datetime
from bson.objectid import ObjectId
from flask import Flask, render_template, request, redirect, url_for, flash, session, jsonify
from flask_pymongo import PyMongo
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from dotenv import load_dotenv
from pymongo.errors import PyMongoError
from bson.errors import InvalidId
OMDB_API_KEY = os.getenv("OMDB_API_KEY")
# Load environment variables
load_dotenv()

# Initialize Flask app
app = Flask(__name__)

# Configuration from .env
app.secret_key = os.environ.get("SECRET_KEY", "secret124")
app.config["MONGO_URI"] = os.environ.get("MONGO_URI")
app.config["UPLOAD_FOLDER"] = os.environ.get("UPLOAD_FOLDER", "./static/images")

# Database connection
try:
    if not app.config["MONGO_URI"]:
        raise ValueError("MONGO_URI not set in environment variables")
    
    mongo = PyMongo(app, connectTimeoutMS=30000, socketTimeoutMS=30000)
    db = mongo.db
    
    # Test connection
    db.command('ping')
    print("✅ MongoDB connected successfully!")
    
    # Initialize collections
    users = db.users
    films = db.films
    reviews = db.reviews
    reports = db.reports
    articles = db.articles
    follows = db.follows
    
    # Create indexes
    users.create_index([("username", 1)], unique=True)
    users.create_index([("email", 1)], unique=True)
    films.create_index([("title", "text")])
    films.create_index([("genres", 1)])

except PyMongoError as e:
    print(f"❌ MongoDB connection failed: {e}")
    exit(1)

# Helper functions
def is_logged_in():
    return 'user_id' in session

def is_admin():
    if is_logged_in():
        user = users.find_one({'_id': ObjectId(session['user_id'])})
        return user and user.get('role') == 'admin'
    return False

def get_current_user():
    if is_logged_in():
        return users.find_one({'_id': ObjectId(session['user_id'])})
    return None

# Routes
@app.route('/import-omdb', methods=['POST'])
def import_omdb_film():
    # Check if user is logged in
    if not is_logged_in():
        flash("You need to login first to review films", "warning")
        return redirect(url_for('login', next=url_for('search_films')))
    
    imdb_id = request.form.get('imdb_id')
    if not imdb_id:
        flash("Invalid film selection", "error")
        return redirect(url_for('search_films'))

    # Cek apakah film sudah ada di database
    existing = films.find_one({'imdb_id': imdb_id})
    if existing:
        return redirect(url_for('film_detail', film_id=existing['_id']))

    # Fetch dari OMDb API
    omdb_url = f"http://www.omdbapi.com/?i={imdb_id}&apikey={OMDB_API_KEY}"
    try:
        response = requests.get(omdb_url).json()

        if response.get('Response') != 'True':
            flash("Film not found in OMDb", "error")
            return redirect(url_for('search_films'))

        # Parse tanggal rilis
        release_date = None
        if response.get('Released') and response.get('Released') != 'N/A':
            try:
                release_date = datetime.strptime(response.get('Released'), "%d %b %Y")
            except:
                release_date = None

        # Simpan ke MongoDB
        film_data = {
            'imdb_id': imdb_id,
            'title': response.get('Title'),
            'year': response.get('Year'),
            'genres': response.get('Genre', '').split(', ') if response.get('Genre') else [],
            'poster_url': response.get('Poster') if response.get('Poster') != 'N/A' else '',
            'release_date': release_date,
            'plot': response.get('Plot'),
            'average_rating': 0,
            'views': 0,
            'created_at': datetime.now()
        }

        inserted = films.insert_one(film_data)
        return redirect(url_for('film_detail', film_id=inserted.inserted_id))

    except Exception as e:
        flash("Error fetching from OMDb", "error")
        return redirect(url_for('search_films'))


@app.route('/user/<username>/followers')
def followers_page(username):
    user = users.find_one({'username': username})
    if not user:
        return render_template('404.html'), 404

    followers_ids = follows.find({'following_id': user['_id']}).distinct('follower_id')
    followers = users.find({'_id': {'$in': followers_ids}})
    
    return render_template('social/followers.html',
                           user=user,
                           followers=followers)
    
@app.route('/your_page')
def for_your_page():
    if not is_logged_in():
        return redirect(url_for('login'))

    current_user_id = ObjectId(session['user_id'])

    # Highlighted film: paling populer
    highlighted_film = films.find_one(sort=[("views", -1)])

    # Trending: top 10 berdasarkan views
    trending_films = list(films.find().sort("views", -1).limit(10))

    # Ambil review terbaru dan isi data user
    all_reviews = reviews.find().sort("created_at", -1).limit(10)
    user_reviews = []
    for r in all_reviews:
        user = users.find_one({'_id': r['user_id']})
        if not user:
            continue
        is_following = follows.find_one({
            'follower_id': current_user_id,
            'following_id': user['_id']
        })
        user_reviews.append({
            '_id': r['_id'],
            'text': r['text'],
            'rating': r['rating'],
            'likes': r.get('likes', 0),
            'dislikes': r.get('dislikes', 0),  
            'comments': 0,
            'user': user,
            'is_following': is_following is not None
        })

    # Ambil data user
    current_user_data = users.find_one({'_id': current_user_id})

    # Watchlist berbasis genre
    watchlist_ids = current_user_data.get('watchlist', [])
    genre_watchlist = {}
    if watchlist_ids:
        watchlist_films = list(films.find({'_id': {'$in': watchlist_ids}}))
        for film in watchlist_films:
            for genre in film.get('genres', []):
                genre_watchlist.setdefault(genre, []).append(film)

    # Custom watchlists
    custom_watchlists = current_user_data.get('custom_watchlists', [])
    for wl in custom_watchlists:
        film_ids = wl.get("film_ids", [])
        wl['films'] = list(films.find({'_id': {'$in': film_ids}}))

    # Update current_user_data agar custom_watchlists memiliki film di dalamnya
    current_user_data['custom_watchlists'] = custom_watchlists

    return render_template(
        'social/for_your_page.html',
        highlighted_film=highlighted_film,
        trending_films=trending_films,
        user_reviews=user_reviews,
        genre_watchlist=genre_watchlist,
        current_user=current_user_data  # penting!
    )


@app.route('/film/<film_id>/toggle-watchlist', methods=['POST'])
def toggle_watchlist(film_id):
    if not is_logged_in():
        return redirect(url_for('login'))

    user_id = ObjectId(session['user_id'])
    film_obj_id = ObjectId(film_id)

    user = users.find_one({'_id': user_id})
    watchlist = user.get('watchlist', [])

    if film_obj_id in watchlist:
        users.update_one({'_id': user_id}, {'$pull': {'watchlist': film_obj_id}})
        flash('Removed from watchlist.', 'info')
    else:
        users.update_one({'_id': user_id}, {'$addToSet': {'watchlist': film_obj_id}})
        flash('Added to watchlist!', 'success')

    return redirect(url_for('film_detail', film_id=film_id))




@app.route('/')
def index():
    popular_films = films.find().sort('views', -1).limit(10)
    new_films = films.find().sort('release_date', -1).limit(10)

    featured_articles_cursor = articles.find().sort('created_at', -1).limit(3)
    featured_articles = [
        {**a, "_id": str(a["_id"])} for a in featured_articles_cursor
    ]

    return render_template('index.html',
                           popular_films=popular_films,
                           new_films=new_films,
                           featured_articles=featured_articles,
                           user=get_current_user())


@app.route('/films')
def film_list():
    all_films = films.find().sort('release_date', -1)
    return render_template('film/list.html', 
                         films=all_films,
                         user=get_current_user())

import requests

@app.route('/search', methods=['GET', 'POST'])
def search_films():
    query = request.args.get('q') or request.form.get('query')
    
    results = []
    if query:
        # Fetch data from OMDb API
        omdb_url = f"http://www.omdbapi.com/?s={query}&apikey={OMDB_API_KEY}&type=movie"
        try:
            response = requests.get(omdb_url)
            data = response.json()
            if data.get('Response') == 'True':
                results = data.get('Search', [])
        except Exception as e:
            flash('Failed to fetch data from OMDb', 'error')
    
    return render_template('film/search.html',
                           results=results,
                           query=query,
                           user=get_current_user())


@app.route('/film/<film_id>')
def film_detail(film_id):
    try:
        # Ambil film dan tambahkan 1 view
        film = films.find_one_and_update(
            {'_id': ObjectId(film_id)},
            {'$inc': {'views': 1}},
            return_document=True
        )
        if not film:
            return render_template('404.html'), 404

        # Ambil semua review terkait film ini
        raw_reviews = reviews.find({'film_id': ObjectId(film_id)}).sort('created_at', -1)

        # Gabungkan data user ke setiap review
        enriched_reviews = []
        for r in raw_reviews:
            user = users.find_one({'_id': r['user_id']})
            if not user:
                continue  

            enriched_reviews.append({
                '_id': r['_id'],
                'text': r['text'],
                'rating': r['rating'],
                'likes': r.get('likes', 0),
                'dislikes': r.get('dislikes', 0),
                'created_at': r['created_at'],
                'user': user  
            })

        # Cek apakah user sudah mereview film ini
        user_reviewed = False
        if is_logged_in():
            user_review = reviews.find_one({
                'film_id': ObjectId(film_id),
                'user_id': ObjectId(session['user_id'])
            })
            user_reviewed = bool(user_review)

        # Kirim ke template
        return render_template('film/detail.html',
                               film=film,
                               reviews=enriched_reviews,  
                               user_reviewed=user_reviewed,
                               current_user=get_current_user())

    except Exception as e:
        print("❌ Error in /film/<film_id>:", e)
        flash('Error loading film details', 'error')
        return redirect(url_for('index'))

@app.route('/edit-profile', methods=['GET', 'POST'])
def edit_profile():
    if not is_logged_in():
        return redirect(url_for('login'))

    user = get_current_user()

    if request.method == 'POST':
        bio = request.form.get('bio', '')
        profile_pic = request.files.get('profile_pic')

        update_data = {'bio': bio}

        if profile_pic and profile_pic.filename != '':
            filename = secure_filename(profile_pic.filename)
            profile_pic.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
            update_data['profile_pic'] = filename

        users.update_one({'_id': user['_id']}, {'$set': update_data})
        flash('Profile updated successfully!', 'success')
        return redirect(url_for('user_profile', username=user['username']))


    return render_template('auth/edit_profile.html', user=user)


@app.route('/film/<film_id>/add-to-watchlist', methods=['POST'])
def add_to_custom_watchlist(film_id):
    if not is_logged_in():
        return redirect(url_for('login'))

    user_id = ObjectId(session['user_id'])
    user = users.find_one({'_id': user_id})
    film_obj_id = ObjectId(film_id)

    selected_name = request.form.get('watchlist_name') or ''
    new_name = (request.form.get('new_watchlist_name') or '').strip()

    final_name = new_name if selected_name == '__new__' and new_name else selected_name.strip()
    
    if not final_name:
        flash("Watchlist name is required", "error")
        return redirect(url_for('film_detail', film_id=film_id))

    custom_watchlists = user.get('custom_watchlists', [])

    for watchlist in custom_watchlists:
        if watchlist['name'].lower() == final_name.lower():
            if film_obj_id in watchlist['film_ids']:
                flash("Film already in this watchlist", "info")
                return redirect(url_for('film_detail', film_id=film_id))
            watchlist['film_ids'].append(film_obj_id)
            break
    else:
        custom_watchlists.append({
            'name': final_name,
            'film_ids': [film_obj_id]
        })

    users.update_one({'_id': user_id}, {'$set': {'custom_watchlists': custom_watchlists}})
    flash(f'Added to "{final_name}" watchlist!', 'success')
    return redirect(url_for('film_detail', film_id=film_id))


@app.route('/film/<film_id>/save-to-watchlist', methods=['POST'])
def save_to_watchlist(film_id):
    if not is_logged_in():
        return redirect(url_for('login'))

    user_id = ObjectId(session['user_id'])
    watchlist_name = request.form.get('watchlist_name', '').strip()

    if not watchlist_name:
        flash("Watchlist name cannot be empty", "error")
        return redirect(url_for('film_detail', film_id=film_id))

    user = users.find_one({'_id': user_id})
    film_oid = ObjectId(film_id)

    # Cek apakah watchlist sudah ada
    found = False
    updated_watchlists = []

    for wl in user.get('custom_watchlists', []):
        if wl['name'].lower() == watchlist_name.lower():
            if film_oid not in wl.get('film_ids', []):
                wl['film_ids'].append(film_oid)
            found = True
        updated_watchlists.append(wl)

    # Kalau belum ada → buat baru
    if not found:
        updated_watchlists.append({
            'name': watchlist_name,
            'film_ids': [film_oid]
        })

    users.update_one({'_id': user_id}, {'$set': {'custom_watchlists': updated_watchlists}})
    flash(f'Added to watchlist "{watchlist_name}"!', 'success')
    return redirect(url_for('film_detail', film_id=film_id))


@app.route('/film/<film_id>/review', methods=['POST'])
def add_review(film_id):
    if not is_logged_in():
        return redirect(url_for('login'))
    
    rating = float(request.form.get('rating'))

    review_text = request.form.get('review')
    
    if rating < 1 or rating > 10 or (rating * 2) % 1 != 0:
        flash('Rating must be from 1 to 10 with 0.5 intervals', 'error')


        return redirect(url_for('film_detail', film_id=film_id))
    
    reviews.insert_one({
        'film_id': ObjectId(film_id),
        'user_id': ObjectId(session['user_id']),
        'rating': rating,
        'text': review_text,
        'likes': 0,
        'dislikes': 0,
        'liked_by': [],        # ✅ Tambahkan ini
        'disliked_by': [],     # ✅ Dan ini
        'is_spoiler': False,
        'created_at': datetime.now()
    })
    
    # Update film average rating
    pipeline = [
        {'$match': {'film_id': ObjectId(film_id)}},
        {'$group': {'_id': None, 'avgRating': {'$avg': '$rating'}}}
    ]
    avg_rating = list(reviews.aggregate(pipeline))[0]['avgRating']
    films.update_one({'_id': ObjectId(film_id)}, {'$set': {'average_rating': avg_rating}})
    
    flash('Review submitted successfully!', 'success')
    return redirect(url_for('film_detail', film_id=film_id))

@app.route('/review/<review_id>/like', methods=['POST'])
def like_review(review_id):
    if not is_logged_in():
        return jsonify({'success': False, 'message': 'Not logged in'}), 401

    user_id = ObjectId(session['user_id'])
    review = reviews.find_one({'_id': ObjectId(review_id)})

    if not review:
        return jsonify({'success': False, 'message': 'Review not found'}), 404

    # Kalau user sudah like → batalin (unlike)
    if user_id in review.get('liked_by', []):
        reviews.update_one(
            {'_id': ObjectId(review_id)},
            {
                '$inc': {'likes': -1},
                '$pull': {'liked_by': user_id}
            }
        )
        return jsonify({'success': True, 'action': 'unlike'})

    # Kalau belum like
    update_ops = {
        '$inc': {'likes': 1},
        '$addToSet': {'liked_by': user_id}
    }

    # Kalau user sebelumnya sudah dislike → hapus juga
    if user_id in review.get('disliked_by', []):
        update_ops['$inc']['dislikes'] = -1
        update_ops['$pull'] = {'disliked_by': user_id}

    reviews.update_one({'_id': ObjectId(review_id)}, update_ops)

    return jsonify({'success': True, 'action': 'like'})


@app.route('/review/<review_id>/dislike', methods=['POST'])
def dislike_review(review_id):
    if not is_logged_in():
        return jsonify({'success': False, 'message': 'Not logged in'}), 401

    user_id = ObjectId(session['user_id'])
    review = reviews.find_one({'_id': ObjectId(review_id)})

    if not review:
        return jsonify({'success': False, 'message': 'Review not found'}), 404

    if user_id in review.get('disliked_by', []):
        reviews.update_one(
            {'_id': ObjectId(review_id)},
            {
                '$inc': {'dislikes': -1},
                '$pull': {'disliked_by': user_id}
            }
        )
        return jsonify({'success': True, 'action': 'undislike'})

    update_ops = {
        '$inc': {'dislikes': 1},
        '$addToSet': {'disliked_by': user_id}
    }

    if user_id in review.get('liked_by', []):
        update_ops['$inc']['likes'] = -1
        update_ops['$pull'] = {'liked_by': user_id}

    reviews.update_one({'_id': ObjectId(review_id)}, update_ops)

    return jsonify({'success': True, 'action': 'dislike'})

@app.route('/review/<review_id>/report', methods=['POST'])
def report_review(review_id):
    if not is_logged_in():
        return jsonify({'success': False, 'message': 'Not logged in'}), 401

    user_id = ObjectId(session['user_id'])
    reason = request.form.get('reason', 'Contains spoiler')

    # Ambil review yang ingin direport
    review = reviews.find_one({'_id': ObjectId(review_id)})
    if not review:
        return jsonify({'success': False, 'message': 'Review not found'}), 404

    # Cegah user mereport review milik sendiri
    if review.get('user_id') == user_id:
        return jsonify({'success': False, 'message': 'You cannot report your own review'}), 403

    # Cek apakah user sudah pernah report review ini
    existing_report = reports.find_one({
        'review_id': ObjectId(review_id),
        'reporter_id': user_id
    })

    if existing_report:
        return jsonify({'success': False, 'message': 'You have already reported this review'}), 400

    # Kalau belum pernah, insert baru
    reports.insert_one({
        'review_id': ObjectId(review_id),
        'reporter_id': user_id,
        'reason': reason,
        'status': 'pending',
        'created_at': datetime.now()
    })

    return jsonify({'success': True})

# Authentication Routes
@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        try:
            username = request.form.get('username')
            email = request.form.get('email')
            password = request.form.get('password')
            
            if users.find_one({'username': username}):
                flash('Username already exists', 'error')
                return redirect(url_for('register'))
            
            if users.find_one({'email': email}):
                flash('Email already registered', 'error')
                return redirect(url_for('register'))
            
            hashed_password = generate_password_hash(password)
            users.insert_one({
                'username': username,
                'email': email,
                'password': hashed_password,
                'role': 'user',
                'bio': '',
                'profile_pic': '',
                'created_at': datetime.now()
            })
            
            flash('Registration successful! Please login.', 'success')
            return redirect(url_for('login'))
        
        except Exception as e:
            flash('Registration failed. Please try again.', 'error')
            return redirect(url_for('register'))
    
    return render_template('auth/register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        try:
            username = request.form.get('username')
            password = request.form.get('password')
            
            user = users.find_one({'username': username})
            if user and check_password_hash(user['password'], password):
                session['user_id'] = str(user['_id'])
                flash('Login successful!', 'success')
                return redirect(url_for('index'))
            else:
                flash('Invalid username or password', 'error')
        
        except Exception as e:
            flash('Login failed. Please try again.', 'error')
    
    return render_template('auth/login.html')

@app.route('/logout')
def logout():
    session.pop('user_id', None)
    flash('You have been logged out', 'info')
    return redirect(url_for('index'))

# Profile Routes
@app.route('/profile', methods=['GET', 'POST'])
def profile():
    if not is_logged_in():
        return redirect(url_for('login'))
    
    try:
        user = get_current_user()
        
        if request.method == 'POST':
            bio = request.form.get('bio')
            profile_pic = request.files.get('profile_pic')
            
            update_data = {'bio': bio}
            
            if profile_pic:
                filename = secure_filename(profile_pic.filename)
                profile_pic.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
                update_data['profile_pic'] = filename
            
            users.update_one(
                {'_id': ObjectId(session['user_id'])},
                {'$set': update_data}
            )
            flash('Profile updated successfully!', 'success')
            return redirect(url_for('profile'))
        
        user_reviews = reviews.find({'user_id': ObjectId(session['user_id'])}).sort('created_at', -1)
        followers_count = follows.count_documents({'following_id': ObjectId(session['user_id'])})
        following_count = follows.count_documents({'follower_id': ObjectId(session['user_id'])})
        
        return render_template('social/profile.html',
                            user=user,
                            reviews=user_reviews,
                            followers_count=followers_count,
                            following_count=following_count)
    
    except Exception as e:
        flash('Error loading profile', 'error')
        return redirect(url_for('index'))

@app.route('/user/<username>')
def user_profile(username):
    user = users.find_one({'username': username})
    if not user:
        return render_template('404.html'), 404

    raw_reviews = reviews.find({'user_id': user['_id']}).sort('created_at', -1)
    user_reviews = []
    for r in raw_reviews:
        film = films.find_one({'_id': r['film_id']})
        if film:
            r['film_id'] = film
        user_reviews.append(r)

    followers_count = follows.count_documents({'following_id': user['_id']})
    following_count = follows.count_documents({'follower_id': user['_id']})

    is_following = False
    if is_logged_in():
        is_following = follows.find_one({
            'follower_id': ObjectId(session['user_id']),
            'following_id': user['_id']
        }) is not None

    return render_template('social/profile.html',
                           user=user,
                           reviews=user_reviews,
                           followers_count=followers_count,
                           following_count=following_count,
                           is_following=is_following,
                           current_user=get_current_user(),
                           follows=follows, 
                           users=users)      



@app.route('/follow/<user_id>', methods=['POST'])
def follow_user(user_id):
    if not is_logged_in():
        return jsonify({'success': False, 'message': 'Not logged in'}), 401
    
    if ObjectId(user_id) == ObjectId(session['user_id']):
        return jsonify({'success': False, 'message': 'Cannot follow yourself'}), 400
    
    existing_follow = follows.find_one({
        'follower_id': ObjectId(session['user_id']),
        'following_id': ObjectId(user_id)
    })
    
    if existing_follow:
        follows.delete_one({'_id': existing_follow['_id']})
        return jsonify({'success': True, 'action': 'unfollow'})
    else:
        follows.insert_one({
            'follower_id': ObjectId(session['user_id']),
            'following_id': ObjectId(user_id),
            'created_at': datetime.now()
        })
        return jsonify({'success': True, 'action': 'follow'})
    
# Admin Routes
@app.route('/admin/dashboard')
def admin_dashboard():
    if not is_admin():
        return redirect(url_for('index'))

    film_count = films.count_documents({})
    review_count = reviews.count_documents({})
    user_count = users.count_documents({})
    article_count = articles.count_documents({})
    report_count = reports.count_documents({'status': 'pending'})

    return render_template('admin/dashboard.html',
                           user=get_current_user(),
                           film_count=film_count,
                           review_count=review_count,
                           user_count=user_count,
                           article_count=article_count,
                           report_count=report_count)


@app.route('/admin/reported-reviews')
def admin_reported_reviews():
    if not is_admin():
        return redirect(url_for('index'))
    
    reported_reviews = reports.find({'status': 'pending'})
    reports_data = []
    
    for report in reported_reviews:
        review = reviews.find_one({'_id': report['review_id']})
        reporter = users.find_one({'_id': report['reporter_id']})
        film = films.find_one({'_id': review['film_id']}) if review else None
        
        if review and reporter and film:
            reports_data.append({
                'report_id': str(report['_id']),
                'review_id': str(review['_id']),
                'review_text': review['text'],
                'film_title': film['title'],
                'reporter_username': reporter['username'],
                'reason': report['reason'],
                'created_at': report['created_at']
            })
    
    return render_template('admin/reported_reviews.html',
                         reports=reports_data,
                         user=get_current_user())

@app.route('/admin/handle-report', methods=['POST'])
def admin_handle_report():
    if not is_admin():
        return jsonify({'success': False, 'message': 'Unauthorized'}), 403
    
    report_id = request.form.get('report_id')
    action = request.form.get('action')
    
    report = reports.find_one({'_id': ObjectId(report_id)})
    if not report:
        return jsonify({'success': False, 'message': 'Report not found'}), 404
    
    if action == 'mark_spoiler':
        reviews.update_one(
            {'_id': report['review_id']},
            {'$set': {'is_spoiler': True}}
        )
    elif action == 'delete':
        reviews.delete_one({'_id': report['review_id']})
    
    reports.update_one(
        {'_id': ObjectId(report_id)},
        {'$set': {'status': 'resolved'}}
    )
    
    return jsonify({'success': True})

@app.route('/admin/articles')
def admin_articles():
    if not is_admin():
        return redirect(url_for('index'))

    all_articles_cursor = articles.find().sort('created_at', -1)
    processed_articles = []

    for article in all_articles_cursor:
        author = users.find_one({'_id': article['author_id']})
        processed_articles.append({
            '_id': str(article['_id']),
            'title': article['title'],
            'created_at': article['created_at'],
            'views': article.get('views', 0),
            'author_username': author['username'] if author else 'Unknown'
        })

    return render_template('admin/articles.html',
                           articles=processed_articles,
                           user=get_current_user())


@app.route('/admin/articles/create', methods=['GET', 'POST'])
def admin_create_article():
    if not is_admin():
        return redirect(url_for('index'))
    
    if request.method == 'POST':
        title = request.form.get('title')
        content = request.form.get('content')
        featured_image = request.files.get('featured_image')
        tags = [tag.strip() for tag in request.form.get('tags', '').split(',') if tag.strip()]
        
        article_data = {
            'title': title,
            'content': content,
            'author_id': ObjectId(session['user_id']),
            'tags': tags,
            'created_at': datetime.now(),
            'views': 0
        }
        
        if featured_image:
            filename = secure_filename(featured_image.filename)
            featured_image.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
            article_data['featured_image'] = filename
        
        articles.insert_one(article_data)
        flash('Article created successfully!', 'success')
        return redirect(url_for('admin_articles'))
    
    return render_template('admin/create_article.html', user=get_current_user())

# Article Routes
@app.route('/article/<article_id>')
def article_detail(article_id):
    try:
        try:
            object_id = ObjectId(article_id)
        except InvalidId:
            flash("Invalid article ID", "error")
            return redirect(url_for('index'))

        article = articles.find_one_and_update(
            {'_id': object_id},
            {'$inc': {'views': 1}},
            return_document=True
        )

        if not article:
            flash("Article not found", "error")
            return redirect(url_for('index'))

        # Dapatkan data penulis
        author = users.find_one({'_id': article['author_id']})
        article['author_username'] = author['username'] if author else 'Unknown'
        article['author_pic'] = author['profile_pic'] if author and author.get('profile_pic') else None

        # Related articles (2 terbaru selain artikel ini)
        related_cursor = articles.find({'_id': {'$ne': object_id}}).sort('created_at', -1).limit(2)
        related_articles = [
            {**a, '_id': str(a['_id'])} for a in related_cursor
        ]

        return render_template('article/detail.html',
                               article=article,
                               related_articles=related_articles,
                               user=get_current_user())
    except Exception as e:
        print("❌ Error loading article:", e)
        flash('Error loading article', 'error')
        return redirect(url_for('index'))
# Error Handlers
@app.errorhandler(404)
def page_not_found(e):
    return render_template('404.html'), 404

@app.errorhandler(500)
def internal_server_error(e):
    return render_template('500.html'), 500

@app.context_processor
def inject_user():
    return dict(current_user=get_current_user())


# Register Jinja global functions
app.jinja_env.globals.update(
    is_logged_in=is_logged_in,
    is_admin=is_admin,
    get_current_user=get_current_user
)

if __name__ == '__main__':
    os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
    app.run(debug=True)

// Bantu Africa Main JavaScript

document.addEventListener('DOMContentLoaded', function() {
    // Initialize tooltips
    var tooltipTriggerList = [].slice.call(document.querySelectorAll('[data-bs-toggle="tooltip"]'));
    var tooltipList = tooltipTriggerList.map(function (tooltipTriggerEl) {
        return new bootstrap.Tooltip(tooltipTriggerEl);
    });

    // Initialize popovers
    var popoverTriggerList = [].slice.call(document.querySelectorAll('[data-bs-toggle="popover"]'));
    var popoverList = popoverTriggerList.map(function (popoverTriggerEl) {
        return new bootstrap.Popover(popoverTriggerEl);
    });

    // Auto-dismiss alerts after 5 seconds
    setTimeout(function() {
        var alerts = document.querySelectorAll('.alert');
        alerts.forEach(function(alert) {
            var bsAlert = new bootstrap.Alert(alert);
            bsAlert.close();
        });
    }, 5000);

    // Form validation
    var forms = document.querySelectorAll('.needs-validation');
    Array.prototype.slice.call(forms).forEach(function(form) {
        form.addEventListener('submit', function(event) {
            if (!form.checkValidity()) {
                event.preventDefault();
                event.stopPropagation();
            }
            form.classList.add('was-validated');
        }, false);
    });

    // Image preview for file inputs
    var imageInputs = document.querySelectorAll('input[type="file"][accept="image/*"]');
    imageInputs.forEach(function(input) {
        input.addEventListener('change', function(e) {
            var file = e.target.files[0];
            if (file) {
                var reader = new FileReader();
                reader.onload = function(e) {
                    var previewId = input.dataset.preview || 'image-preview';
                    var preview = document.getElementById(previewId);
                    if (preview) {
                        preview.src = e.target.result;
                        preview.style.display = 'block';
                    }
                };
                reader.readAsDataURL(file);
            }
        });
    });

    // Like button functionality (global)
    document.addEventListener('click', function(e) {
        if (e.target.closest('.like-btn')) {
            const button = e.target.closest('.like-btn');
            const postId = button.dataset.postId;
            const likeCountSpan = button.querySelector('.like-count');
            const heartIcon = button.querySelector('i');
            
            fetch(`/api/post/${postId}/like`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                }
            })
            .then(response => response.json())
            .then(data => {
                if (likeCountSpan) {
                    likeCountSpan.textContent = data.like_count;
                }
                
                if (data.liked) {
                    heartIcon.className = 'fas fa-heart';
                    button.classList.remove('btn-outline-primary');
                    button.classList.add('btn-primary', 'liked');
                } else {
                    heartIcon.className = 'far fa-heart';
                    button.classList.remove('btn-primary', 'liked');
                    button.classList.add('btn-outline-primary');
                }
            })
            .catch(error => console.error('Error:', error));
        }
    });

    // Infinite scroll for feed
    let isLoading = false;
    let currentPage = 1;
    let hasMore = true;

    function loadMorePosts() {
        if (isLoading || !hasMore) return;
        
        isLoading = true;
        currentPage++;
        
        fetch(`/api/feed?page=${currentPage}`)
            .then(response => response.json())
            .then(data => {
                if (data.posts.length > 0) {
                    const postsContainer = document.getElementById('posts-container');
                    data.posts.forEach(post => {
                        const postHTML = createPostHTML(post);
                        postsContainer.insertAdjacentHTML('beforeend', postHTML);
                    });
                } else {
                    hasMore = false;
                }
                isLoading = false;
            })
            .catch(error => {
                console.error('Error loading posts:', error);
                isLoading = false;
            });
    }

    // Check if user has scrolled to bottom
    window.addEventListener('scroll', function() {
        if (window.innerHeight + window.scrollY >= document.body.offsetHeight - 500) {
            loadMorePosts();
        }
    });

    // Create post HTML function
    function createPostHTML(post) {
        return `
        <div class="card mb-4 post-card fade-in" data-post-id="${post.id}">
            <div class="card-body">
                <div class="d-flex justify-content-between align-items-start mb-3">
                    <div class="d-flex align-items-center">
                        <img src="/static/uploads/${post.author.profile_image}" 
                             class="rounded-circle me-2" width="40" height="40"
                             onerror="this.src='/static/images/default.jpg'">
                        <div>
                            <h6 class="mb-0">
                                <a href="/profile/${post.author.username}" class="text-decoration-none">
                                    ${post.author.username}
                                </a>
                            </h6>
                            <small class="text-muted">${post.created_at}</small>
                        </div>
                    </div>
                </div>

                ${post.title ? `<h5 class="card-title">${post.title}</h5>` : ''}
                
                <p class="card-text">${post.content}</p>
                
                ${post.image ? `
                <div class="text-center my-3">
                    <img src="/static/uploads/${post.image}" class="img-fluid rounded" alt="Post image">
                </div>
                ` : ''}

                <div class="d-flex justify-content-between mt-3">
                    <div>
                        <button class="btn btn-sm ${post.liked ? 'btn-primary liked' : 'btn-outline-primary'} like-btn" 
                                data-post-id="${post.id}">
                            <i class="${post.liked ? 'fas' : 'far'} fa-heart"></i>
                            <span class="like-count">${post.like_count}</span> Likes
                        </button>
                        <a href="/post/${post.id}" class="btn btn-sm btn-outline-secondary">
                            <i class="far fa-comment"></i>
                            <span class="comment-count">${post.comment_count}</span> Comments
                        </a>
                    </div>
                </div>
            </div>
        </div>
        `;
    }

    // Add fade-in animation to new content
    const observer = new IntersectionObserver((entries) => {
        entries.forEach(entry => {
            if (entry.isIntersecting) {
                entry.target.classList.add('fade-in');
            }
        });
    });

    document.querySelectorAll('.post-card').forEach(card => {
        observer.observe(card);
    });

    // Search functionality
    const searchInput = document.querySelector('input[name="q"]');
    if (searchInput) {
        let searchTimeout;
        searchInput.addEventListener('input', function() {
            clearTimeout(searchTimeout);
            searchTimeout = setTimeout(() => {
                if (this.value.length >= 2) {
                    window.location.href = `/search?q=${encodeURIComponent(this.value)}`;
                }
            }, 500);
        });
    }
});

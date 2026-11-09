# Register your blueprints
    from app.admin.routes import admin_bp
    from app.auth.routes import auth_bp
    from app.main.routes import main_bp  # Or your primary UI blueprint
    
    app.register_blueprint(admin_bp)
    app.register_blueprint(auth_bp, url_prefix="/auth")
    app.register_blueprint(main_bp)

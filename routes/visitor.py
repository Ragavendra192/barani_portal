# Visitor approval module has been removed from the application.
from flask import Blueprint

visitor_bp = Blueprint("visitor", __name__, url_prefix="/visitor")

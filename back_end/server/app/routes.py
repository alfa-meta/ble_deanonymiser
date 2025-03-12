from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required, create_access_token, get_jwt_identity, verify_jwt_in_request, decode_token
from flask_bcrypt import Bcrypt
from flask_socketio import emit, disconnect
import threading
import subprocess
from config import Config
from .models import Device, User, db
from . import socketio

main_bp = Blueprint('main', __name__)
bcrypt = Bcrypt()

PASSWORD_SALT = Config.PASSWORD_SALT
processes = {}

if not Config.PASSWORD_SALT or not Config.JWT_SECRET_KEY:
    raise ValueError(f"Environment variables PASSWORD_SALT is {PASSWORD_SALT} JWT_SECRET_KEY is {Config.JWT_SECRET_KEY} must be set.")


@main_bp.route("/register", methods=["POST"])
def register():
    try:
        data = request.get_json()
        if not data or "email" not in data or "password" not in data or "username" not in data:
            return jsonify({"message": "Missing required fields"}), 400

        if User.query.filter_by(email=data["email"]).first():
            return jsonify({"message": "Email already registered"}), 400

        hashed_pw = bcrypt.generate_password_hash(data["password"] + PASSWORD_SALT).decode("utf-8")

        user = User(username=data["username"], email=data["email"], password=hashed_pw)
        db.session.add(user)
        db.session.commit()
        
        access_token = create_access_token(identity=user.email)
        return jsonify(access_token=access_token), 200
    except Exception as e:
        print("Error in register:", e)
        return jsonify({"message": "An error occurred, please try again later"}), 500


@main_bp.route("/login", methods=["POST"])
def login():
    try:
        data = request.get_json()
        if not data or "email" not in data or "password" not in data:
            return jsonify({"message": "Email and password are required"}), 400

        user = User.query.filter_by(email=data["email"]).first()

        if user and bcrypt.check_password_hash(user.password, data["password"] + PASSWORD_SALT):
            access_token = create_access_token(identity=user.email)
            return jsonify(access_token=access_token), 200

        return jsonify({"message": "Invalid credentials"}), 401
    except Exception as e:
        print("Error in login:", e)
        return jsonify({"message": "An error occurred, please try again later"}), 500


@main_bp.route("/protected", methods=["GET"])
@jwt_required()
def protected():
    try:
        current_user_email = get_jwt_identity()
        user = User.query.filter_by(email=current_user_email).first()

        if user:
            return jsonify({"email": user.email}), 200

        return jsonify({"message": "User not found"}), 404
    except Exception as e:
        print(f"Error in protected route: {e}")
        return jsonify({"message": "An error occurred, please try again later"}), 500


@main_bp.route("/users", methods=["GET"])
@jwt_required()
def get_all_users():
    try:
        users = User.query.all()
        user_list = [{"uid": user.uid, "username": user.username, "email": user.email} for user in users]
        return jsonify(user_list), 200
    except Exception as e:
        print(f"Error in get_all_users: {e}")
        return jsonify({"message": "An error occurred, please try again later"}), 500


@main_bp.route("/delete_user/<int:user_id>", methods=["DELETE"])
@jwt_required()
def delete_user(user_id):
    try:
        user = User.query.get(user_id)
        if not user:
            return jsonify({"message": "User not found"}), 404

        db.session.delete(user)
        db.session.commit()
        return jsonify({"message": "User deleted successfully"}), 200
    except Exception as e:
        print(f"Error in delete_user: {e}")
        return jsonify({"message": "An error occurred, please try again later"}), 500


@main_bp.route('/devices', methods=['GET'])
@jwt_required()
def get_all_devices():
    try:
        devices = Device.query.all()
        device_list = [{"mac_address": d.mac_address, "device_name": d.device_name, "last_seen": d.last_seen, "email": d.email} for d in devices]
        return jsonify(device_list), 200
    except Exception as e:
        print(f"Error in get_all_devices: {e}")
        return jsonify({"message": "An error occurred, please try again later"}), 500


@socketio.on("websocket_handle_connect")
def websocket_handle_connect():
    token = request.args.get("token")  # Extract token from query params
    if not token:
        print("Missing token, disconnecting WebSocket.")
        disconnect()
        return

    try:
        decoded_token = decode_token(token)  # Manually decode the JWT token
        user_email = decoded_token.get("sub")  # Extract user identity
        if not user_email:
            raise ValueError("Invalid token payload")

        print(f"User {user_email} connected via WebSocket")
    except Exception as e:
        print(f"WebSocket connection error: {e}")
        disconnect()


@socketio.on("websocket_start_scan")
@jwt_required()
def websocket_start_scan():
    try:
        verify_jwt_in_request()
        user_email = get_jwt_identity()

        process = subprocess.Popen(
            ["python", "../../sniffer/main.py"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
        processes[user_email] = process

        def stream_output():
            try:
                for line in iter(process.stdout.readline, ''):
                    if process.poll() is not None:  # Check if the process has ended
                        break
                    emit("scan_update", {"message": line.strip()})
            except Exception as e:
                print(f"Error streaming output: {e}")
            finally:
                process.stdout.close()

        threading.Thread(target=stream_output, daemon=True).start()

        emit("scan_update", {"message": "Scanning started", "pid": process.pid})
    except Exception as e:
        print(f"Error in start_scan: {e}")
        emit("scan_error", {"message": "Error starting scan", "error": str(e)})


@socketio.on("websocket_handle_disconnect")
@jwt_required()
def websocket_handle_disconnect():
    try:
        user_email = get_jwt_identity()
        if user_email in processes:
            process = processes.pop(user_email)
            process.terminate()
            process.wait()  # Ensure process is properly terminated
            print(f"Stopped process {process.pid}")
    except Exception as e:
        print(f"Error in handle_disconnect: {e}")

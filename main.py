#!/usr/bin/env python3

import curses
import textwrap
import requests
import websocket
import json
import threading
import time
import sys
from urllib.parse import urlencode

# Configuration
SERVER_URL = "https://bigblackoiledup.men"
WS_URL = "wss://bigblackoiledup.men/ws"

# Global state
token = None
user = None
halls = []
current_hall = None
current_rooms = []
current_messages = []
messages_offset = 0  # for lazy loading
has_more_messages = True
ws = None
ws_connected = False

# UI modes
MODE_MAIN, MODE_CREATE_HALL, MODE_JOIN_HALL, MODE_SETTINGS = 0, 1, 2, 3
current_mode = MODE_MAIN

# UI state
FOCUS_HALLS, FOCUS_ROOMS, FOCUS_CHAT, FOCUS_INPUT, FOCUS_SETTINGS = 0, 1, 2, 3, 4
focus = FOCUS_HALLS
sel_hall_idx = 0
sel_room_idx = 0
chat_scroll = 0
chat_at_bottom = True  # track if user is viewing bottom messages
input_buffer = ""
status_message = ""

# Form state for different modes
form_input = ""
form_input2 = ""
form_focus = 0

def api_request(method, endpoint, data=None):
    """Make authenticated API request"""
    headers = {}
    if token:
        headers['Authorization'] = f'Bearer {token}'
    if data:
        headers['Content-Type'] = 'application/json'
    
    try:
        url = f"{SERVER_URL}{endpoint}"
        if method == "GET":
            response = requests.get(url, headers=headers)
        elif method == "POST":
            response = requests.post(url, headers=headers, json=data)
        else:
            return None
        
        if response.status_code == 200:
            return response.json()
        else:
            return {"error": f"HTTP {response.status_code}: {response.text}"}
    except Exception as e:
        return {"error": str(e)}

def login_flow():
    """Handle login outside of curses"""
    global token, user
    

    print(r"                                                          _ _ ")
    print(r"  ___ ___  _ __ ___  _ __ ___   ___  _ __  ___        ___| (_)")
    print(r" / __/ _ \| '_ ` _ \| '_ ` _ \ / _ \| '_ \/ __|_____ / __| | |")
    print(r"| (_| (_) | | | | | | | | | | | (_) | | | \__ \_____| (__| | |")
    print(r" \___\___/|_| |_| |_|_| |_| |_|\___/|_| |_|___/      \___|_|_|")
    print(r"                                                              ")


    choice = input("(1) Login or (2) Register? [1/2]: ").strip()
    username = input("Username: ").strip()
    password = input("Password: ").strip()
    
    if not username or not password:
        print("Username and password required")
        return False
    
    endpoint = "/api/login" if choice == "1" else "/api/register"
    data = {"username": username, "password": password}
    
    result = api_request("POST", endpoint, data)
    if "error" in result:
        print(f"Error: {result['error']}")
        return False
    
    token = result["token"]
    user = result["user"]
    print(f"Success! Logged in as {user['username']}")
    return True

def load_halls():
    """Load user's halls from API"""
    global halls
    result = api_request("GET", "/api/halls")
    if "error" not in result:
        halls = result.get("halls", [])
    else:
        halls = []

def load_rooms(hall_id):
    """Load rooms for a hall"""
    global current_rooms, sel_room_idx
    result = api_request("GET", f"/api/rooms/{hall_id}")
    if "error" not in result:
        current_rooms = result.get("rooms", [])
    else:
        current_rooms = []
    
    # Ensure room selection is valid
    if current_rooms and sel_room_idx >= len(current_rooms):
        sel_room_idx = 0

def load_messages(room_id, prepend=False):
    """Load recent messages for a room"""
    global current_messages, messages_offset, has_more_messages
    
    if not prepend:
        # loading new room - start fresh
        messages_offset = 0
        has_more_messages = True
        
        result = api_request("GET", f"/api/messages/{room_id}?limit=50&offset=0")
        if "error" not in result:
            messages = result.get("messages", [])
            current_messages = messages
            has_more_messages = len(messages) == 50
            messages_offset = len(messages)
        else:
            current_messages = []
            has_more_messages = False
    else:
        # load older messages and prepend them
        result = api_request("GET", f"/api/messages/{room_id}?limit=50&offset={messages_offset}")
        if "error" not in result:
            messages = result.get("messages", [])
            if messages:
                # prepend older messages to the beginning
                current_messages = messages + current_messages
                messages_offset += len(messages)
                has_more_messages = len(messages) == 50
            else:
                has_more_messages = False

def is_user_admin():
    """Check if current user is admin of current hall"""
    return current_hall and current_hall.get("owner_id") == user.get("id")

def create_hall():
    """Create new hall with full name and short name"""
    global status_message
    if not form_input or not form_input2:
        status_message = "Both names required"
        return False
    
    if len(form_input2) != 5:
        status_message = "Short name must be exactly 5 characters"
        return False
    
    data = {"name": f"{form_input2}:{form_input}"}  # Store as "SHORT:Full Name"
    result = api_request("POST", "/api/halls/create", data)
    if "error" in result:
        status_message = f"Error: {result['error']}"
        return False
    
    load_halls()
    status_message = "Hall created successfully"
    return True

def join_hall():
    """Join hall with invite code"""
    global status_message
    if not form_input:
        status_message = "Invite code required"
        return False
    
    data = {"invite_code": form_input}
    result = api_request("POST", "/api/halls/join", data)
    if "error" in result:
        status_message = f"Error: {result['error']}"
        return False
    
    load_halls()
    status_message = "Joined hall successfully"
    return True

def create_room():
    """Create new room in current hall"""
    global status_message
    if not form_input or not current_hall:
        status_message = "Room name and hall required"
        return False
    
    data = {"hall_id": current_hall["id"], "name": form_input}
    result = api_request("POST", "/api/rooms/create", data)
    if "error" in result:
        status_message = f"Error: {result['error']}"
        return False
    
    load_rooms(current_hall["id"])
    status_message = "Room created successfully"
    return True

def delete_room():
    """Delete room from current hall"""
    global status_message
    if not form_input or not current_hall:
        status_message = "Room name and hall required"
        return False
    
    # Find room by name
    room_to_delete = None
    for room in current_rooms:
        if room["name"].lower() == form_input.lower():
            room_to_delete = room
            break
    
    if not room_to_delete:
        status_message = "Room not found"
        return False
    
    result = api_request("POST", f"/api/rooms/{room_to_delete['id']}/delete", {})
    if "error" in result:
        status_message = f"Error: {result['error']}"
        return False
    
    load_rooms(current_hall["id"])
    status_message = "Room deleted successfully"
    return True

def give_admin_rights():
    """Give admin rights to user"""
    global status_message
    if not form_input or not current_hall:
        status_message = "Username and hall required"
        return False
    
    data = {"username": form_input, "hall_id": current_hall["id"]}
    result = api_request("POST", "/api/halls/give-admin", data)
    if "error" in result:
        status_message = f"Error: {result['error']}"
        return False
    
    status_message = "Admin rights granted successfully"
    return True

def regenerate_invite():
    """Generate new invite code for current hall"""
    global status_message, current_hall
    if not current_hall:
        status_message = "No hall selected"
        return False
    
    result = api_request("POST", f"/api/halls/{current_hall['id']}/regenerate-invite", {})
    if "error" in result:
        status_message = f"Error: {result['error']}"
        return False
    
    # Update the current hall's invite code
    if "invite_code" in result:
        current_hall["invite_code"] = result["invite_code"]
    
    status_message = "New invite code generated"
    return True

def delete_hall():
    """Delete current hall"""
    global status_message, current_hall, halls, sel_hall_idx
    if not current_hall:
        status_message = "No hall selected"
        return False
    
    result = api_request("POST", f"/api/halls/{current_hall['id']}/delete", {})
    if "error" in result:
        status_message = f"Error: {result['error']}"
        return False
    
    # Reload halls and reset selection
    load_halls()
    current_hall = None
    sel_hall_idx = 0
    
    status_message = "Hall deleted successfully"
    return True

def leave_hall():
    """Leave current hall"""
    global status_message, current_hall, halls, sel_hall_idx
    if not current_hall:
        status_message = "No hall selected"
        return False
    
    result = api_request("POST", "/api/halls/leave", {"hall_id": current_hall['id']})
    if "error" in result:
        status_message = f"Error: {result['error']}"
        return False
    
    # Reload halls and reset selection
    load_halls()
    current_hall = None
    sel_hall_idx = 0
    
    status_message = "Left hall successfully"
    return True

def start_websocket():
    """Start WebSocket connection in background thread"""
    global ws, ws_connected
    
    def on_message(ws, message):
        try:
            data = json.loads(message)
            if data.get("type") == "new_message":
                msg_data = data["data"]["message"]
                current_messages.append(msg_data)
        except:
            pass
    
    def on_open(ws):
        global ws_connected
        ws_connected = True
        if current_hall and current_rooms and sel_room_idx < len(current_rooms):
            room = current_rooms[sel_room_idx]
            join_msg = {
                "type": "join_room",
                "data": {"hall_id": current_hall["id"], "room_id": room["id"]}
            }
            ws.send(json.dumps(join_msg))
    
    def on_close(ws, close_status_code, close_msg):
        global ws_connected
        ws_connected = False
    
    def on_error(ws, error):
        global ws_connected
        ws_connected = False
    
    try:
        url = f"{WS_URL}?token={token}"
        ws = websocket.WebSocketApp(url,
                                    on_message=on_message,
                                    on_open=on_open,
                                    on_close=on_close,
                                    on_error=on_error)
        ws.run_forever()
    except:
        ws_connected = False

def send_message(content):
    """Send message via WebSocket"""
    if ws and ws_connected and current_rooms and sel_room_idx < len(current_rooms):
        room = current_rooms[sel_room_idx]
        msg = {
            "type": "send_message",
            "data": {"room_id": room["id"], "content": content}
        }
        try:
            ws.send(json.dumps(msg))
        except:
            pass

def join_room_ws():
    """Join current room via WebSocket"""
    if ws and ws_connected and current_hall and current_rooms and sel_room_idx < len(current_rooms):
        room = current_rooms[sel_room_idx]
        msg = {
            "type": "join_room", 
            "data": {"hall_id": current_hall["id"], "room_id": room["id"]}
        }
        try:
            ws.send(json.dumps(msg))
        except:
            pass

def ellipsize(s, width):
    if width <= 0:
        return ""
    if len(s) <= width:
        return s.ljust(width)
    if width <= 3:
        return "." * width
    return s[:width-3] + "..."

def center_write(stdscr, y, x_start, width, text, attr=0):
    if width <= 0:
        return
    t = text[:width]
    start = x_start + max(0, (width - len(t)) // 2)
    try:
        stdscr.addstr(y, start, t, attr)
    except curses.error:
        pass

def draw_status_top_right(stdscr, w):
    """Draw status in top-right corner"""
    try:
        # Show SETTINGS button for all users - highlight only when focused
        status_parts = [f"WS:{'✓' if ws_connected else '✗'}"]
        if user:
            status_parts.append(user['username'])
        status_parts.append("SETTINGS")
        
        # Determine how much space we need
        status_text = " | ".join(status_parts[:-1])  # Everything except SETTINGS
        settings_text = status_parts[-1]
        
        # Draw the status part first
        if len(status_text) > 0:
            x_status = max(0, w - len(status_text) - len(settings_text) - 4)  # Leave space for " | SETTINGS"
            stdscr.addstr(0, x_status, status_text + " | ")
        
        # Draw SETTINGS with appropriate highlighting
        settings_attr = curses.A_REVERSE if focus == FOCUS_SETTINGS else curses.A_BOLD
        x_settings = max(0, w - len(settings_text) - 1)
        stdscr.addstr(0, x_settings, settings_text, settings_attr)
    except curses.error:
        pass

def draw_borders_and_titles(stdscr, h, w, halls_w, rooms_w, chat_w):
    try:
        # Top border
        stdscr.addch(0, 0, curses.ACS_ULCORNER)
        stdscr.hline(0, 1, curses.ACS_HLINE, halls_w)
        stdscr.addch(0, 1 + halls_w, curses.ACS_TTEE)
        stdscr.hline(0, 1 + halls_w + 1, curses.ACS_HLINE, rooms_w)
        stdscr.addch(0, 1 + halls_w + 1 + rooms_w, curses.ACS_TTEE)
        if chat_w > 1:
            stdscr.hline(0, 1 + halls_w + 1 + rooms_w + 1, curses.ACS_HLINE, chat_w - 1)
        if w > 1:
            stdscr.addch(0, w - 1, curses.ACS_URCORNER)

        # Titles
        center_write(stdscr, 0, 1, halls_w, "HALLS", curses.A_REVERSE if focus == FOCUS_HALLS else 0)
        center_write(stdscr, 0, 1 + halls_w + 1, rooms_w, "ROOMS", curses.A_REVERSE if focus == FOCUS_ROOMS else 0)
        center_write(stdscr, 0, 1 + halls_w + 1 + rooms_w + 1, chat_w - 1, "CHAT", curses.A_REVERSE if focus == FOCUS_CHAT else 0)

        # Vertical borders
        for y in range(1, h - 1):
            stdscr.addch(y, 0, curses.ACS_VLINE)
            stdscr.addch(y, 1 + halls_w, curses.ACS_VLINE)
            stdscr.addch(y, 1 + halls_w + 1 + rooms_w, curses.ACS_VLINE)
            if w > 1:
                stdscr.addch(y, w - 1, curses.ACS_VLINE)

        # Bottom border with proper corner
        if h > 1:
            stdscr.addch(h - 1, 0, curses.ACS_LLCORNER)
            stdscr.hline(h - 1, 1, curses.ACS_HLINE, halls_w)
            stdscr.addch(h - 1, 1 + halls_w, curses.ACS_BTEE)
            stdscr.hline(h - 1, 1 + halls_w + 1, curses.ACS_HLINE, rooms_w)
            stdscr.addch(h - 1, 1 + halls_w + 1 + rooms_w, curses.ACS_BTEE)
            # Draw bottom line all the way to the corner
            chat_start_x = 1 + halls_w + 1 + rooms_w + 1
            if w > chat_start_x:
                line_length = w - 1 - chat_start_x
                stdscr.hline(h - 1, chat_start_x, curses.ACS_HLINE, line_length)
            # Add bottom-right corner
            if w > 1 and h > 1:
                stdscr.addch(h - 1, w - 1, curses.ACS_LRCORNER)
    except curses.error:
        pass

def draw_input_separator(stdscr, h, w, halls_w, rooms_w, chat_w):
    try:
        y = h - 3
        join_x = 1 + halls_w + 1 + rooms_w
        chat_x = join_x + 1
        # Use ├ (left tee) where rooms and input intersect
        stdscr.addch(y, join_x, curses.ACS_LTEE)
        # Draw horizontal line all the way to the right edge
        if w > join_x + 1:
            line_length = w - 1 - chat_x
            stdscr.hline(y, chat_x, curses.ACS_HLINE, line_length)
        # Use ┤ (right tee) where right edge and input collide
        if w > 1:
            stdscr.addch(y, w - 1, curses.ACS_RTEE)
        center_write(stdscr, y, chat_x, chat_w - 1, "INPUT", curses.A_REVERSE if focus == FOCUS_INPUT else 0)
    except curses.error:
        pass

def get_hall_display_name(hall):
    """Extract 5-char name from hall name (SHORT:Full Name format)"""
    name = hall["name"]
    if ":" in name:
        return name.split(":", 1)[0]
    return name[:5].ljust(5)

def draw_halls(stdscr, h, halls_w):
    max_rows = h - 6  # Leave room for +Join and +NEW
    for i in range(max_rows):
        y = 1 + i
        x = 1
        try:
            if i < len(halls):
                label = ellipsize(get_hall_display_name(halls[i]), halls_w - 1)
                attr = curses.A_BOLD if i == sel_hall_idx else 0
                stdscr.addstr(y, x, label, attr)
            else:
                stdscr.addstr(y, x, " " * (halls_w - 1))
        except curses.error:
            pass
    
    # Draw +Join and +NEW options
    try:
        join_y = h - 4
        new_y = h - 3
        
        join_attr = curses.A_BOLD if sel_hall_idx == len(halls) else 0
        new_attr = curses.A_BOLD if sel_hall_idx == len(halls) + 1 else 0
        
        stdscr.addstr(join_y, x, ellipsize("+Join", halls_w - 1), join_attr)
        stdscr.addstr(new_y, x, ellipsize("+ NEW", halls_w - 1), new_attr)
    except curses.error:
        pass

def draw_rooms(stdscr, h, halls_w, rooms_w):
    max_rows = h - 3  # Remove space for +new button
    for i in range(max_rows):
        y = 1 + i
        x = 1 + halls_w + 1
        try:
            if i < len(current_rooms):
                label = ellipsize(current_rooms[i]["name"], rooms_w - 1)
                attr = curses.A_BOLD if i == sel_room_idx else 0
                stdscr.addstr(y, x, label, attr)
            else:
                stdscr.addstr(y, x, " " * (rooms_w - 1))
        except curses.error:
            pass

def draw_chat(stdscr, h, w, halls_w, rooms_w, chat_w):
    global chat_scroll, chat_at_bottom
    x = 1 + halls_w + 1 + rooms_w + 1
    y_start = 1
    y_end = h - 4
    height = max(0, y_end - y_start)

    lines = []
    wrap_width = max(1, chat_w - 2)
    
    for msg in current_messages:
        username = msg.get("username", "unknown")
        content = msg.get("content", "")
        lines += textwrap.wrap(f"{username}: {content}", wrap_width)

    max_scroll = max(0, len(lines) - height)
    
    # auto-scroll to bottom if user was at bottom and there are new messages
    if chat_at_bottom:
        chat_scroll = max_scroll
    
    chat_scroll = max(0, min(chat_scroll, max_scroll))
    
    # update bottom tracking based on current scroll position
    chat_at_bottom = (chat_scroll >= max_scroll)
    
    visible = lines[chat_scroll: chat_scroll + height]

    for i in range(height):
        y = y_start + i
        try:
            text = (visible[i] if i < len(visible) else "").ljust(max(0, chat_w - 2))
            stdscr.addstr(y, x, text[:chat_w - 2])
        except curses.error:
            pass

def draw_input(stdscr, h, halls_w, rooms_w, chat_w):
    x = 1 + halls_w + 1 + rooms_w + 1
    y = h - 2
    width = max(0, chat_w - 2)
    try:
        stdscr.addstr(y, x, " " * width)
        display_text = input_buffer[:width]
        stdscr.addstr(y, x, display_text)
    except curses.error:
        pass

def draw_form_screen(stdscr, h, w, title, field1_label, field2_label=None):
    """Draw form screen for hall creation/joining"""
    stdscr.erase()
    
    try:
        # Title
        center_write(stdscr, 2, 0, w, title, curses.A_BOLD)
        
        # Field 1
        stdscr.addstr(5, 4, f"{field1_label}:")
        field1_attr = curses.A_REVERSE if form_focus == 0 else 0
        stdscr.addstr(6, 4, f"[{form_input.ljust(40)}]", field1_attr)
        
        # Field 2 (if provided)
        if field2_label:
            stdscr.addstr(8, 4, f"{field2_label}:")
            field2_attr = curses.A_REVERSE if form_focus == 1 else 0
            stdscr.addstr(9, 4, f"[{form_input2.ljust(40)}]", field2_attr)
        
        # Instructions
        stdscr.addstr(h - 4, 4, "Enter: Submit | ESC: Cancel | Tab: Next field")
        
        if status_message:
            stdscr.addstr(h - 2, 4, status_message)
            
    except curses.error:
        pass

def draw_settings_screen(stdscr, h, w):
    """Draw settings panel screen"""
    stdscr.erase()
    
    try:
        center_write(stdscr, 2, 0, w, "SETTINGS", curses.A_BOLD)
        
        if current_hall:
            # Show current hall information
            hall_name = current_hall["name"].split(":", 1)[-1] if ":" in current_hall["name"] else current_hall["name"]
            short_name = get_hall_display_name(current_hall)
            stdscr.addstr(4, 4, f"Current Hall: {hall_name} ({short_name})", curses.A_BOLD)
            stdscr.addstr(5, 4, f"Invite Code: {current_hall['invite_code']}")
            stdscr.addstr(6, 4, f"Room Count: {len(current_rooms)}")
            
            # Show all available options
            stdscr.addstr(8, 4, "Options:", curses.A_BOLD)
            options = ["Leave Hall"]
            
            if is_user_admin():
                # Add admin options for hall owners
                options.extend(["Create Room", "Delete Room", "Delete Hall", "Give Admin Rights", "Generate New Invite"])
            
            options.append("Back to Chat")
            
            for i, option in enumerate(options):
                attr = curses.A_REVERSE if form_focus == i else 0
                stdscr.addstr(10 + i, 6, f"{i+1}. {option}", attr)
                
            # Input fields based on selected option (only for admin options that need input)
            if is_user_admin():
                input_y = 17
                if form_focus == 1:  # Create room
                    stdscr.addstr(input_y, 4, "Room name:")
                    stdscr.addstr(input_y + 1, 4, f"[{form_input.ljust(30)}]", curses.A_REVERSE)
                elif form_focus == 2:  # Delete room
                    stdscr.addstr(input_y, 4, "Room name to delete:")
                    stdscr.addstr(input_y + 1, 4, f"[{form_input.ljust(30)}]", curses.A_REVERSE)
                elif form_focus == 4:  # Give admin rights
                    stdscr.addstr(input_y, 4, "Username:")
                    stdscr.addstr(input_y + 1, 4, f"[{form_input.ljust(30)}]", curses.A_REVERSE)
            else:
                # Non-admin users can leave the hall
                stdscr.addstr(8, 4, "Options:", curses.A_BOLD)
                options = ["Leave Hall", "Back to Chat"]
                for i, option in enumerate(options):
                    attr = curses.A_REVERSE if form_focus == i else 0
                    stdscr.addstr(10 + i, 6, f"{i+1}. {option}", attr)
        else:
            # No hall selected
            stdscr.addstr(4, 4, "No hall selected. Please select a hall first.")
            back_attr = curses.A_REVERSE if form_focus == 0 else 0
            stdscr.addstr(6, 4, "1. Back to Chat", back_attr)
        
        stdscr.addstr(h - 4, 4, "Arrow keys: Navigate | Enter: Select | ESC: Back")
        
        if status_message:
            stdscr.addstr(h - 2, 4, status_message)
            
    except curses.error:
        pass

def handle_main_key(key):
    global focus, sel_hall_idx, sel_room_idx, chat_scroll, input_buffer, chat_at_bottom
    global current_hall, current_mode, form_input, form_input2, form_focus
    global has_more_messages, current_messages, current_rooms
    
    if key == 9:  # Tab
        max_focus = FOCUS_SETTINGS  # Settings available to all users
        focus = (focus + 1) % (max_focus + 1)
        return
    if key == curses.KEY_BTAB:  # Shift+Tab
        max_focus = FOCUS_SETTINGS  # Settings available to all users
        focus = (focus - 1) % (max_focus + 1)
        return

    if key == curses.KEY_LEFT:
        if focus in (FOCUS_ROOMS, FOCUS_CHAT): 
            focus -= 1
        elif focus == FOCUS_INPUT: 
            focus = FOCUS_CHAT
        elif focus == FOCUS_SETTINGS:
            focus = FOCUS_CHAT
        return
    if key == curses.KEY_RIGHT:
        if focus in (FOCUS_HALLS, FOCUS_ROOMS): 
            focus += 1
        elif focus == FOCUS_CHAT: 
            focus = FOCUS_SETTINGS  # Settings available to all users
        return

    if key == 27:  # ESC to quit
        raise SystemExit

    if focus == FOCUS_HALLS:
        total_items = len(halls) + 2  # +Join and +NEW
        if key == curses.KEY_DOWN:
            sel_hall_idx = (sel_hall_idx + 1) % total_items
            if sel_hall_idx < len(halls):
                hall_changed()
        elif key == curses.KEY_UP:
            sel_hall_idx = (sel_hall_idx - 1) % total_items
            if sel_hall_idx < len(halls):
                hall_changed()
        elif key in (curses.KEY_ENTER, 10, 13):
            if sel_hall_idx == len(halls):  # +Join
                current_mode = MODE_JOIN_HALL
                form_input = ""
                form_focus = 0
            elif sel_hall_idx == len(halls) + 1:  # +NEW
                current_mode = MODE_CREATE_HALL
                form_input = ""
                form_input2 = ""
                form_focus = 0

    elif focus == FOCUS_ROOMS:
        if current_rooms:
            total_items = len(current_rooms)  # Removed +new option
            if key == curses.KEY_DOWN:
                sel_room_idx = (sel_room_idx + 1) % total_items if total_items > 0 else 0
                if sel_room_idx < len(current_rooms):
                    room_changed()
            elif key == curses.KEY_UP:
                sel_room_idx = (sel_room_idx - 1) % total_items if total_items > 0 else 0
                if sel_room_idx < len(current_rooms):
                    room_changed()
            elif key in (curses.KEY_ENTER, 10, 13):
                # Just select the room, no +new option
                if sel_room_idx < len(current_rooms):
                    room_changed()

    elif focus == FOCUS_CHAT:
        if key == curses.KEY_DOWN: 
            chat_scroll += 1
            chat_at_bottom = False  # user manually scrolled
        elif key == curses.KEY_UP: 
            # check if we need to load more messages before scrolling up
            if chat_scroll <= 5 and has_more_messages and current_rooms and sel_room_idx < len(current_rooms):
                room = current_rooms[sel_room_idx]
                old_msg_count = len(current_messages)
                load_messages(room["id"], prepend=True)
                # adjust scroll to maintain position after prepending messages
                new_msg_count = len(current_messages)
                chat_scroll += new_msg_count - old_msg_count
            chat_scroll -= 1
            chat_at_bottom = False  # user manually scrolled
        elif key == curses.KEY_NPAGE: 
            chat_scroll += 5
            chat_at_bottom = False  # user manually scrolled
        elif key == curses.KEY_PPAGE: 
            # check if we need to load more messages before scrolling up
            if chat_scroll <= 10 and has_more_messages and current_rooms and sel_room_idx < len(current_rooms):
                room = current_rooms[sel_room_idx]
                old_msg_count = len(current_messages)
                load_messages(room["id"], prepend=True)
                # adjust scroll to maintain position after prepending messages
                new_msg_count = len(current_messages)
                chat_scroll += new_msg_count - old_msg_count
            chat_scroll -= 1
            chat_at_bottom = False  # user manually scrolled

    elif focus == FOCUS_INPUT:
        if key in (curses.KEY_ENTER, 10, 13):
            if input_buffer.strip():
                send_message(input_buffer.strip())
                input_buffer = ""
            return
        if key in (curses.KEY_BACKSPACE, 127, 8):
            input_buffer = input_buffer[:-1]
            return
        if 32 <= key <= 126:
            input_buffer += chr(key)
            return
    
    elif focus == FOCUS_SETTINGS:
        if key in (curses.KEY_ENTER, 10, 13):
            current_mode = MODE_SETTINGS
            form_focus = 0
            form_input = ""

def handle_form_key(key):
    global form_input, form_input2, form_focus, current_mode, status_message
    
    if key == 27:  # ESC - cancel
        current_mode = MODE_MAIN
        form_input = ""
        form_input2 = ""
        status_message = ""
        return
    
    if key == 9:  # Tab - next field (only for create hall)
        if current_mode == MODE_CREATE_HALL:
            form_focus = (form_focus + 1) % 2
        return
    
    if key in (curses.KEY_ENTER, 10, 13):
        if current_mode == MODE_CREATE_HALL:
            if create_hall():
                current_mode = MODE_MAIN
                form_input = ""
                form_input2 = ""
        elif current_mode == MODE_JOIN_HALL:
            if join_hall():
                current_mode = MODE_MAIN
                form_input = ""
        return
    
    if key in (curses.KEY_BACKSPACE, 127, 8):
        if current_mode == MODE_CREATE_HALL:
            if form_focus == 0:
                form_input = form_input[:-1]
            else:
                form_input2 = form_input2[:-1]
        else:
            form_input = form_input[:-1]
        return
    
    if 32 <= key <= 126:
        if current_mode == MODE_CREATE_HALL:
            if form_focus == 0:
                form_input += chr(key)
            else:
                if len(form_input2) < 5:  # Limit short name to 5 chars
                    form_input2 += chr(key)
        else:
            if len(form_input) < 16:  # Limit invite codes to 16 chars
                form_input += chr(key)

def handle_settings_key(key):
    global form_focus, form_input, current_mode, status_message
    
    if key == 27:  # ESC - back
        current_mode = MODE_MAIN
        form_input = ""
        status_message = ""
        return
    
    if current_hall:
        # Calculate available options based on user role
        options = ["Leave Hall"]
        if is_user_admin():
            options.extend(["Create Room", "Delete Room", "Delete Hall", "Give Admin Rights", "Generate New Invite"])
        options.append("Back to Chat")
        
        max_options = len(options)
        
        if key == curses.KEY_UP:
            form_focus = (form_focus - 1) % max_options
            form_input = ""
        elif key == curses.KEY_DOWN:
            form_focus = (form_focus + 1) % max_options
            form_input = ""
        
        if key in (curses.KEY_ENTER, 10, 13):
            if form_focus == 0:  # Leave Hall (always first option)
                if leave_hall():
                    current_mode = MODE_MAIN
                    form_input = ""
            elif is_user_admin() and form_focus == 1:  # Create room
                if form_input.strip():
                    if create_room():
                        form_input = ""
            elif is_user_admin() and form_focus == 2:  # Delete room
                if form_input.strip():
                    if delete_room():
                        form_input = ""
            elif is_user_admin() and form_focus == 3:  # Delete hall
                if delete_hall():
                    current_mode = MODE_MAIN
                    form_input = ""
            elif is_user_admin() and form_focus == 4:  # Give admin rights
                if form_input.strip():
                    if give_admin_rights():
                        form_input = ""
            elif is_user_admin() and form_focus == 5:  # Generate invite
                if regenerate_invite():
                    pass  # No input needed
            elif form_focus == len(options) - 1:  # Back (always last option)
                current_mode = MODE_MAIN
                form_input = ""
                status_message = ""
            return
        
        # Handle input for text fields (admin options that need input)
        if is_user_admin() and form_focus in [1, 2, 4]:  # Create room, Delete room, Give admin
            if key in (curses.KEY_BACKSPACE, 127, 8):
                form_input = form_input[:-1]
            elif 32 <= key <= 126:
                char = chr(key)
                if form_focus == 1:  # Create room - special handling
                    # Convert to lowercase
                    char = char.lower()
                    # Convert spaces to dashes
                    if char == " ":
                        char = "-"
                    # Filter out invalid symbols (!@#$%^&*()_=) but allow dashes
                    if char not in "!@#$%^&*()_=":
                        # Handle dash spacing rules
                        if char == "-" and (form_input == "" or form_input.endswith("-")):
                            pass  # don't add dash at start or double dashes
                        elif len(form_input) < 19:  # limit to 19 chars (server adds # = 20 total)
                            form_input += char
                else:
                    # Normal input for other fields
                    form_input += char
    else:
        # No hall selected - only Back option
        if key in (curses.KEY_ENTER, 10, 13):
            current_mode = MODE_MAIN
            form_input = ""
            status_message = ""

def hall_changed():
    """Called when hall selection changes"""
    global current_hall, sel_room_idx
    if halls and sel_hall_idx < len(halls):
        current_hall = halls[sel_hall_idx]
        load_rooms(current_hall["id"])
        sel_room_idx = 0
        room_changed()

def room_changed():
    """Called when room selection changes"""
    global chat_scroll, chat_at_bottom
    if current_rooms and sel_room_idx < len(current_rooms):
        room = current_rooms[sel_room_idx]
        load_messages(room["id"])
        chat_scroll = 0
        chat_at_bottom = True  # start at bottom for new rooms
        join_room_ws()

def tui_main(stdscr):
    global status_message, current_mode
    
    curses.curs_set(0)
    stdscr.nodelay(True)
    stdscr.keypad(True)
    stdscr.timeout(100)

    # Load initial data
    load_halls()
    if halls:
        hall_changed()

    # Start WebSocket in background
    if token:
        ws_thread = threading.Thread(target=start_websocket, daemon=True)
        ws_thread.start()

    while True:
        stdscr.erase()
        h, w = stdscr.getmaxyx()

        # Handle different UI modes
        if current_mode == MODE_CREATE_HALL:
            draw_form_screen(stdscr, h, w, "CREATE HALL", "Full Name", "Short Name (5 chars)")
        elif current_mode == MODE_JOIN_HALL:
            draw_form_screen(stdscr, h, w, "JOIN HALL", "Invite Code (16 chars)")
        elif current_mode == MODE_SETTINGS:
            draw_settings_screen(stdscr, h, w)
        else:
            # Main TUI mode
            min_w = 50
            min_h = 10
            if w < min_w or h < min_h:
                try:
                    msg = f"Terminal too small. Resize to at least {min_w}x{min_h}."
                    stdscr.addstr(0, 0, msg[:w-1])
                except curses.error:
                    pass
                stdscr.noutrefresh()
                curses.doupdate()
                key = stdscr.getch()
                if key == 27:
                    break
                continue

            halls_w = 7  # 5 chars + 2 borders
            rooms_w = 20
            chat_w = max(10, w - (halls_w + rooms_w + 4))

            draw_borders_and_titles(stdscr, h, w, halls_w, rooms_w, chat_w)
            draw_input_separator(stdscr, h, w, halls_w, rooms_w, chat_w)
            draw_halls(stdscr, h, halls_w)
            draw_rooms(stdscr, h, halls_w, rooms_w)
            draw_chat(stdscr, h, w, halls_w, rooms_w, chat_w)
            draw_input(stdscr, h, halls_w, rooms_w, chat_w)
            draw_status_top_right(stdscr, w)

        stdscr.noutrefresh()
        curses.doupdate()

        try:
            key = stdscr.getch()
            if key == curses.KEY_RESIZE:
                continue
            elif key != -1:
                if current_mode == MODE_MAIN:
                    handle_main_key(key)
                elif current_mode in (MODE_CREATE_HALL, MODE_JOIN_HALL):
                    handle_form_key(key)
                elif current_mode == MODE_SETTINGS:
                    handle_settings_key(key)
        except SystemExit:
            break
        except Exception as e:
            status_message = f"Error: {str(e)}"

def main():
    # Check if server is running
    try:
        response = requests.get(f"{SERVER_URL}/api/halls", timeout=2)
    except:
        print("Error: Server not running.")
        sys.exit(1)

    # Login before starting TUI
    if not login_flow():
        print("Login failed")
        sys.exit(1)

    print("Starting chat client... (Press ESC to quit)")
    time.sleep(1)
    
    try:
        curses.wrapper(tui_main)
    except KeyboardInterrupt:
        pass
    finally:
        if ws:
            ws.close()

if __name__ == "__main__":
    main()

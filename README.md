# Chat App CLI Client

Terminal-based chat client for the commons chat application.

## Setup

1. Start the backend server:
```bash
cd ../commons-api
./chatapp
```

2. Install Python dependencies:
```bash
../venv/bin/pip install -r requirements.txt
```

3. Run the CLI client:
```bash
../venv/bin/python main.py
```

## Usage

### Initial Setup
- Choose (1) Login or (2) Register
- Enter username and password
- The TUI will start automatically

### Navigation
- **TAB** / **Shift+TAB**: Switch between panels (Halls → Rooms → Chat → Input)
- **Arrow Keys**: Navigate within panels
- **ESC**: Quit application

### Panels
- **HALLS**: Your joined halls (servers)
- **ROOMS**: Rooms within selected hall  
- **CHAT**: Messages in selected room (scroll with arrows/page keys)
- **INPUT**: Type messages (Enter to send)

### Features
- Real-time message updates via WebSocket
- Automatic room subscription when switching
- Connection status indicator
- Presence heartbeat (keeps you online)

## Testing

Run the demo without TUI:
```bash
../venv/bin/python demo.py
```

Test API integration:
```bash
../venv/bin/python test_client.py
```

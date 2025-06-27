from fastapi import FastAPI, HTTPException, Depends, Cookie
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from typing import List, Optional, Dict
from uuid import uuid4
from pydantic import BaseModel, Field
import secrets


# ==================== Models ====================

class UserLogin(BaseModel):
    username: str = Field(..., description="Desired username")


class UserOut(BaseModel):
    username: str
    user_id: str


class Move(BaseModel):
    row: int = Field(..., ge=0, le=2, description="Row of the move (0-2)")
    col: int = Field(..., ge=0, le=2, description="Column of the move (0-2)")


class GameCreate(BaseModel):
    player_x: Optional[str] = Field(
        None,
        description="User ID for player X"
    )
    player_o: Optional[str] = Field(
        None,
        description="User ID for player O"
    )


class GameOut(BaseModel):
    game_id: str
    board: List[List[str]]
    current_turn: str
    state: str
    winner: Optional[str]
    player_x: Optional[str]
    player_o: Optional[str]
    moves: List[Move]


class GameHistoryOut(BaseModel):
    games: List[GameOut]


# ==================== In-Memory Stores ====================

users: Dict[str, Dict] = {}  # user_id: {username, history}
games: Dict[str, Dict] = {}  # game_id: {board, current_turn, moves, player_x, player_o, state, winner}
user_sessions: Dict[str, str] = {}  # session_token: user_id


# ==================== Constants ====================

EMPTY_BOARD = [["" for _ in range(3)] for _ in range(3)]
STATE_ONGOING = "ongoing"
STATE_WIN = "win"
STATE_DRAW = "draw"
STATE_WAITING = "waiting"
VALID_SYMBOLS = ["X", "O"]

openapi_tags = [
    {
        "name": "Users",
        "description": "User registration, login and management endpoints"
    },
    {
        "name": "Game",
        "description": "Endpoints for Tic Tac Toe game play"
    },
    {
        "name": "History",
        "description": "Endpoints for retrieving game history"
    },
]


# ==================== FastAPI Setup ====================

app = FastAPI(
    title="Tic Tac Toe Backend",
    description=(
        "API for a multiplayer Tic Tac Toe game, supporting user registration, "
        "session authentication, game play, and "
        "history."
    ),
    version="1.0.0",
    openapi_tags=openapi_tags
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # For development; restrict in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ==================== Auth Helpers ====================

def generate_user_id() -> str:
    return str(uuid4())


def generate_session_token() -> str:
    return secrets.token_urlsafe(32)


def get_current_user(session_token: Optional[str] = Cookie(None)) -> Optional[dict]:
    if not session_token:
        return None
    user_id = user_sessions.get(session_token)
    if user_id:
        return users.get(user_id)
    return None


# ==================== Game Logic ====================

def new_board():
    return [["" for _ in range(3)] for _ in range(3)]


def check_winner(board: List[List[str]]) -> Optional[str]:
    """Returns 'X', 'O', or None."""
    for i in range(3):
        # Check rows/cols
        if board[i][0] == board[i][1] == board[i][2] != "":
            return board[i][0]
        if board[0][i] == board[1][i] == board[2][i] != "":
            return board[0][i]
    # Diagonals
    if board[0][0] == board[1][1] == board[2][2] != "":
        return board[0][0]
    if board[0][2] == board[1][1] == board[2][0] != "":
        return board[0][2]
    return None


def is_draw(board: List[List[str]]) -> bool:
    return all(board[r][c] != "" for r in range(3) for c in range(3))


# ==================== User Management Endpoints ====================

# PUBLIC_INTERFACE
@app.post("/users/register", response_model=UserOut, tags=["Users"], summary="Register a new user")
def register_user(login: UserLogin):
    """
    Registers a new user and returns authentication information.
    """
    # Prevent duplicate usernames
    for user in users.values():
        if user["username"] == login.username:
            raise HTTPException(status_code=400, detail="Username already taken")
    user_id = generate_user_id()
    users[user_id] = {"username": login.username, "history": []}
    return UserOut(username=login.username, user_id=user_id)


# PUBLIC_INTERFACE
@app.post("/users/login", response_model=UserOut, tags=["Users"], summary="User login")
def user_login(login: UserLogin):
    """
    Logs in a user by username. If username does not exist, registers it automatically.
    Returns a session token cookie for authentication.
    """
    for uid, user in users.items():
        if user["username"] == login.username:
            user_id = uid
            break
    else:
        user_id = generate_user_id()
        users[user_id] = {"username": login.username, "history": []}

    session_token = generate_session_token()
    user_sessions[session_token] = user_id
    response = JSONResponse(
        content={"username": login.username, "user_id": user_id}
    )
    response.set_cookie(key="session_token", value=session_token, httponly=True)
    return response


# PUBLIC_INTERFACE
@app.post("/users/logout", tags=["Users"], summary="User logout")
def user_logout(session_token: Optional[str] = Cookie(None)):
    """
    Logs the user out by removing their session.
    """
    if session_token and session_token in user_sessions:
        del user_sessions[session_token]
    response = JSONResponse(content={"success": True})
    response.delete_cookie("session_token")
    return response


# PUBLIC_INTERFACE
@app.get("/users/me", response_model=UserOut, tags=["Users"], summary="Get current user")
def get_me(user=Depends(get_current_user)):
    """
    Returns details of the currently authenticated user.
    """
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return UserOut(
        username=user["username"],
        user_id=[k for k, v in users.items() if v == user][0]
    )


# ==================== Game Endpoints ====================

def _get_symbol_for_user(game: dict, user_id: str) -> Optional[str]:
    if game["player_x"] == user_id:
        return "X"
    elif game["player_o"] == user_id:
        return "O"
    else:
        return None


# PUBLIC_INTERFACE
@app.post("/game/create", response_model=GameOut, tags=["Game"], summary="Create a new game")
def create_game(game: GameCreate, user=Depends(get_current_user)):
    """
    Creates a new Tic Tac Toe game. Players may optionally be assigned at creation.
    """
    game_id = str(uuid4())
    player_x = game.player_x or (user and [k for k in users if users[k] == user][0])
    player_o = game.player_o or None
    board = new_board()
    games[game_id] = {
        "board": board,
        "current_turn": "X",
        "moves": [],
        "player_x": player_x,
        "player_o": player_o,
        "state": STATE_WAITING if not player_o else STATE_ONGOING,
        "winner": None,
        "game_id": game_id
    }
    return GameOut(
        game_id=game_id,
        board=board,
        current_turn="X",
        state=games[game_id]["state"],
        winner=None,
        player_x=player_x,
        player_o=player_o,
        moves=[]
    )


# PUBLIC_INTERFACE
@app.post("/game/join/{game_id}", response_model=GameOut, tags=["Game"], summary="Join a game")
def join_game(game_id: str, user=Depends(get_current_user)):
    """
    Joins an existing game as Player O. Player X is assumed to be present.
    """
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    if game_id not in games:
        raise HTTPException(status_code=404, detail="Game not found")
    game = games[game_id]
    if not game["player_o"]:
        game["player_o"] = [k for k in users if users[k] == user][0]
        if game["state"] == STATE_WAITING:
            game["state"] = STATE_ONGOING
    else:
        raise HTTPException(status_code=400, detail="Game already has two players")
    return GameOut(
        game_id=game_id,
        board=game["board"],
        current_turn=game["current_turn"],
        state=game["state"],
        winner=game["winner"],
        player_x=game["player_x"],
        player_o=game["player_o"],
        moves=game["moves"]
    )


# PUBLIC_INTERFACE
@app.get("/game/{game_id}", response_model=GameOut, tags=["Game"], summary="Get game state")
def get_game_state(game_id: str):
    """
    Retrieve full state of a Tic Tac Toe game.
    """
    if game_id not in games:
        raise HTTPException(status_code=404, detail="Game not found")
    game = games[game_id]
    return GameOut(
        game_id=game_id,
        board=game["board"],
        current_turn=game["current_turn"],
        state=game["state"],
        winner=game["winner"],
        player_x=game["player_x"],
        player_o=game["player_o"],
        moves=game["moves"]
    )


# PUBLIC_INTERFACE
@app.post("/game/{game_id}/move", response_model=GameOut, tags=["Game"], summary="Make a move")
def make_move(game_id: str, move: Move, user=Depends(get_current_user)):
    """
    Make a move as the authenticated user.
    """
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    if game_id not in games:
        raise HTTPException(status_code=404, detail="Game not found")
    game = games[game_id]
    if game["state"] not in [STATE_ONGOING]:
        raise HTTPException(
            status_code=400,
            detail=f"Game not in progress: {game['state']}"
        )
    user_id = [k for k in users if users[k] == user][0]
    symbol = _get_symbol_for_user(game, user_id)
    if symbol != game["current_turn"]:
        raise HTTPException(status_code=403, detail="It's not your turn")
    if not (0 <= move.row <= 2 and 0 <= move.col <= 2):
        raise HTTPException(status_code=400, detail="Move out of bounds")
    if game["board"][move.row][move.col] != "":
        raise HTTPException(status_code=400, detail="Cell already occupied")

    # Apply move
    game["board"][move.row][move.col] = symbol
    game["moves"].append(move)
    winner = check_winner(game["board"])

    if winner:
        game["state"] = STATE_WIN
        game["winner"] = winner
        # Record to users' history
        if game["player_x"]:
            users[game["player_x"]]["history"].append(game_id)
        if game["player_o"]:
            users[game["player_o"]]["history"].append(game_id)
    elif is_draw(game["board"]):
        game["state"] = STATE_DRAW
        game["winner"] = None
        if game["player_x"]:
            users[game["player_x"]]["history"].append(game_id)
        if game["player_o"]:
            users[game["player_o"]]["history"].append(game_id)
    else:
        game["current_turn"] = "O" if symbol == "X" else "X"

    return GameOut(
        game_id=game_id,
        board=game["board"],
        current_turn=game["current_turn"],
        state=game["state"],
        winner=game["winner"],
        player_x=game["player_x"],
        player_o=game["player_o"],
        moves=game["moves"]
    )


# PUBLIC_INTERFACE
@app.post("/game/{game_id}/reset", response_model=GameOut, tags=["Game"], summary="Restart game")
def reset_game(game_id: str, user=Depends(get_current_user)):
    """
    Resets the board of a completed game (for replay with same players).
    """
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    if game_id not in games:
        raise HTTPException(status_code=404, detail="Game not found")
    game = games[game_id]
    if not (game["player_x"] and game["player_o"]):
        raise HTTPException(
            status_code=400,
            detail="Game does not have two players"
        )
    game["board"] = new_board()
    game["current_turn"] = "X"
    game["state"] = STATE_ONGOING
    game["winner"] = None
    game["moves"] = []
    return GameOut(
        game_id=game_id,
        board=game["board"],
        current_turn=game["current_turn"],
        state=game["state"],
        winner=None,
        player_x=game["player_x"],
        player_o=game["player_o"],
        moves=[]
    )


# PUBLIC_INTERFACE
@app.get("/game/list", response_model=List[GameOut], tags=["Game"], summary="List all games")
def list_games():
    """
    Lists all game instances (for debugging/demo purposes).
    """
    return [
        GameOut(
            game_id=g_id,
            board=g["board"],
            current_turn=g["current_turn"],
            state=g["state"],
            winner=g["winner"],
            player_x=g["player_x"],
            player_o=g["player_o"],
            moves=g["moves"],
        )
        for g_id, g in games.items()
    ]


# ==================== Game History ====================

# PUBLIC_INTERFACE
@app.get("/history", response_model=GameHistoryOut, tags=["History"], summary="Get current user's game history")
def get_history(user=Depends(get_current_user)):
    """
    Retrieves the history of game IDs played by the current user.
    """
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    user_id = [k for k in users if users[k] == user][0]
    history_game_ids = users[user_id]["history"]
    hist_games = [
        GameOut(
            game_id=g_id,
            board=games[g_id]["board"],
            current_turn=games[g_id]["current_turn"],
            state=games[g_id]["state"],
            winner=games[g_id]["winner"],
            player_x=games[g_id]["player_x"],
            player_o=games[g_id]["player_o"],
            moves=games[g_id]["moves"],
        )
        for g_id in history_game_ids
        if g_id in games
    ]
    return GameHistoryOut(games=hist_games)


# ==================== Healthcheck ====================

# PUBLIC_INTERFACE
@app.get("/", tags=["Users"], summary="Health check")
def health_check():
    """Healthcheck endpoint for liveness probe."""
    return {"message": "Healthy"}

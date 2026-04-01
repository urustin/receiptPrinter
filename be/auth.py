import os
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from authlib.integrations.starlette_client import OAuth
from jose import jwt, JWTError

ALGORITHM = "HS256"
SECRET_KEY = os.environ["SECRET_KEY"]
REDIRECT_URI = os.environ.get("REDIRECT_URI")  # e.g. https://print.ericfromkorea.com/auth/callback

oauth = OAuth()
oauth.register(
    name="google",
    client_id=os.environ["GOOGLE_CLIENT_ID"],
    client_secret=os.environ["GOOGLE_CLIENT_SECRET"],
    server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
    client_kwargs={"scope": "openid email profile"},
)

_bearer = HTTPBearer()

router = APIRouter(prefix="/auth")


def require_auth(credentials: HTTPAuthorizationCredentials = Depends(_bearer)):
    try:
        return jwt.decode(credentials.credentials, SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid or expired token")


@router.get("/login")
async def login(request: Request):
    redirect_uri = REDIRECT_URI or str(request.url_for("auth_callback"))
    return await oauth.google.authorize_redirect(request, redirect_uri)


@router.get("/callback", name="auth_callback")
async def auth_callback(request: Request):
    token = await oauth.google.authorize_access_token(request)
    user = token["userinfo"]
    jwt_token = jwt.encode(
        {
            "email": user["email"],
            "name": user["name"],
            "picture": user.get("picture", ""),
        },
        SECRET_KEY,
        algorithm=ALGORITHM,
    )
    return RedirectResponse(f"/?token={jwt_token}")


@router.get("/me")
async def me(user=Depends(require_auth)):
    return user

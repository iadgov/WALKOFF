from starlette.requests import Request
from fastapi import APIRouter, Depends
from http import HTTPStatus
import logging

from sqlalchemy.orm import Session
from fastapi import HTTPException

from api.security import (create_access_token, create_refresh_token, get_jwt_identity,
                          get_raw_jwt, decode_token, verify_jwt_refresh_token_in_request)
from api.fastapi_config import FastApiConfig
from api.server.db import get_db, get_mongo_c, get_mongo_d
from api.server.db.tokens import revoke_token, AuthModel, TokenModel
from api.server.utils.problems import ProblemException, InvalidInputException
from motor.motor_asyncio import AsyncIOMotorCollection, AsyncIOMotorDatabase

token_problem_title = 'Could not grant access token.'

invalid_credentials_problem = ProblemException(
    HTTPStatus.UNAUTHORIZED,
    token_problem_title,
    'Invalid username or password.'
)

user_deactivated_problem = ProblemException(
    HTTPStatus.UNAUTHORIZED,
    token_problem_title,
    'User is deactivated.'
)

router = APIRouter()
logger = logging.getLogger("API")


def _authenticate_and_grant_tokens(request: Request, users_col: AsyncIOMotorCollection, creds: AuthModel, with_refresh=False):
    if not (creds.username and creds.password):
        raise invalid_credentials_problem

    user = users_col.find_one({"username": creds.username}, projection={'_id': False})

    if user is None:
        raise invalid_credentials_problem

    if not user.active:
        raise user_deactivated_problem

    if user.verify_password(creds.password):
        response = {'access_token': create_access_token(user=user, fresh=True)}
        if with_refresh:
            user.login(request.client.host)
            response['refresh_token'] = create_refresh_token(user=user)
        return response
    else:
        raise invalid_credentials_problem


@router.post("/login")
def login(creds: AuthModel, request: Request, users_col: AsyncIOMotorCollection = Depends(get_mongo_c)):
    return _authenticate_and_grant_tokens(request=request, users_col=users_col, creds=creds, with_refresh=True)


# def fresh_login(creds: AuthModel, request: Request, users_col: AsyncIOMotorCollection = Depends(get_mongo_c)):
#     return _authenticate_and_grant_tokens(request=request, users_col=users_col, creds=creds)


@router.post("/refresh")
def refresh(request: Request, walkoff_db: AsyncIOMotorDatabase = Depends(get_mongo_d)):
    verify_jwt_refresh_token_in_request(walkoff_db=walkoff_db, request=request)
    current_user_id = get_jwt_identity(request)

    user = db_session.query(User).filter_by(id=current_user_id).first()
    if user is None:
        revoke_token(db_session=db_session, decoded_token=get_raw_jwt(request))
        raise ProblemException(
            HTTPStatus.UNAUTHORIZED,
            "Could not grant access token.",
            f"User {current_user_id} from refresh JWT identity could not be found.")
    if user.active:
        return {'access_token': create_access_token(identity=current_user_id, user=user)}, HTTPStatus.CREATED
    else:
        raise user_deactivated_problem


@router.post("/logout")
def logout(json_in: TokenModel, request: Request, db_session: Session = Depends(get_db)):
    data = dict(json_in)
    refresh_token = data.get('refresh_token', None) if data else None
    if refresh_token is None:
        raise ProblemException(HTTPStatus.BAD_REQUEST, 'Could not logout.', 'A refresh token is required to logout.')
    decoded_refresh_token = decode_token(refresh_token)
    refresh_token_identity = decoded_refresh_token[FastApiConfig.JWT_IDENTITY_CLAIM]
    user_id = get_jwt_identity(request)
    if user_id == refresh_token_identity:
        user = db_session.query(User).filter(User.id == user_id).first()
        if user is not None:
            user.logout(db_session=db_session)
        revoke_token(db_session=db_session, decoded_token=decode_token(refresh_token))
        return None, HTTPStatus.NO_CONTENT
    else:
        raise ProblemException(
            HTTPStatus.BAD_REQUEST,
            'Could not logout.',
            'The identity of the refresh token does not match the identity of the authentication token.')


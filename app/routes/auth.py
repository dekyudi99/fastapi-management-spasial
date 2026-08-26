from fastapi import APIRouter, Form, Depends, HTTPException, status
from config.database import get_db
from sqlalchemy.orm import Session
from models.users import Users
from services.password_service import hash_password, verify_password
from services.jwt_service import create_access_token
from fastapi.security import OAuth2PasswordRequestForm

router = APIRouter(prefix="/auth", tags=["Auth"])

@router.post("/register", status_code=status.HTTP_201_CREATED)
def register(
    username: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
    db: Session =  Depends(get_db)
):  
    if len(password) < 8:
        raise HTTPException(status_code=400, detail="Password mainimal 8 karakter.")

    user = Users(
        username=username,
        email=email,
        password=hash_password(password),
    )

    try:
        db.add(user)
        db.commit()
        return {
            "success": True,
            "detail": "Register is succesfull!"
        }
    except Exception as e:
        db.rollback()
        print(f"Gagal menyimpan data karena {e}")
        raise HTTPException(status_code=500, detail=f"Gagal menyimpan data, karena {e}")
    
@router.post("/login")
def login(
    formdata: OAuth2PasswordRequestForm = Depends(),
    # email: str = Form(...),
    # password: str = Form(...),
    db: Session = Depends(get_db)
):
    username = formdata.username
    password = formdata.password

    try:
        user = db.query(Users).filter(Users.username == username).first()
    except Exception as e:
        raise HTTPException(status_code=500, detail=e)
    
    if not user:
        raise HTTPException(status_code=404, detail="Wrong, who are you?")
        
    valid = verify_password(password, user.password)
    
    if not valid:
        raise HTTPException(status_code=401, detail="Wrong, who are you!")
    
    payload = {
        "sub": user.username,
        "email": user.email,
        "role": user.role
    }
    
    access_token = create_access_token(data=payload)
    
    return{
        "success": True,
        "detail": f"Welcome {user.username}",
        "access_token": access_token,
        "type": "bearer"
    }
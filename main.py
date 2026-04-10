from fastapi import FastAPI, Request, Form, Depends, HTTPException, Cookie, Response
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from sqlalchemy import create_engine, Column, String, Integer, Boolean, ForeignKey, DateTime, func, text
from sqlalchemy.orm import sessionmaker, Session, declarative_base
from passlib.context import CryptContext
from itsdangerous import URLSafeSerializer
import os
import csv
import io

# 1. 환경 변수 설정
DATABASE_URL = os.getenv("DATABASE_URL")

if DATABASE_URL is None:
    DATABASE_URL = "sqlite:///./test.db"
    print("경고: DATABASE_URL 환경 변수를 찾을 수 없습니다. 임시 DB를 사용합니다.")

# postgres:// 를 postgresql:// 로 안전하게 교체
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

# 파라미터 충돌 방지 (주소 뒤에 불필요한 설정값 제거)
if "?" in DATABASE_URL and "sqlite" not in DATABASE_URL:
    DATABASE_URL = DATABASE_URL.split("?")[0]

# 2. 엔진 생성 (연결 끊김 방지 옵션 추가)
if DATABASE_URL.startswith("sqlite"):
    engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
else:
    engine = create_engine(
        DATABASE_URL, 
        connect_args={"sslmode": "require"},
        pool_pre_ping=True,  # DB 연결 살아있는지 자동 확인
        pool_recycle=300     # 5분마다 연결 갱신
    )

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
SECRET_KEY = "dankook_engineering_secret"
serializer = URLSafeSerializer(SECRET_KEY)

# 관리자 설정 및 초기 상태 변수
ADMIN_STUDENTS = ["32244983"]
system_notice = "DKU 공대 크레딧 시스템에 오신 것을 환영합니다!" 
event_state = {"is_active": False, "name": "", "amount": 0}

# --- DB 모델 ---
class Student(Base):
    __tablename__ = "students"
    student_id = Column(String, primary_key=True, index=True)
    name = Column(String)
    department = Column(String)
    grade = Column(Integer)
    password_hash = Column(String, nullable=True)
    is_verified = Column(Boolean, default=False)
    total_credits = Column(Integer, default=0)
    phone_number = Column(String, nullable=True)

class CreditLog(Base):
    __tablename__ = "credit_logs"
    id = Column(Integer, primary_key=True, index=True)
    student_id = Column(String, ForeignKey("students.student_id"))
    event_name = Column(String)
    amount = Column(Integer)
    created_at = Column(DateTime, default=func.now())

# 🚨 Supabase 권한 에러(500) 방지를 위해 삭제/주석 처리!
# Base.metadata.create_all(bind=engine)

app = FastAPI()

# 🚨 템플릿 경로를 절대 경로로 설정 (경로 인식 에러 완벽 차단)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if not os.path.exists(os.path.join(BASE_DIR, "static")): 
    os.makedirs(os.path.join(BASE_DIR, "static"))
app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))

def get_db():
    db = SessionLocal()
    try: yield db
    finally: db.close()

# Render 생사 확인(Health Check) 방어 코드
@app.head("/")
async def ping():
    return Response(status_code=200)

# --- 일반 사용자 로직 ---
@app.get("/", response_class=HTMLResponse)
async def login_page(request: Request, user_session: str = Cookie(None), db: Session = Depends(get_db)):
    if user_session:
        try:
            data = serializer.loads(user_session)
            user = db.query(Student).filter(Student.student_id == data["student_id"]).first()
            if user:
                if not user.phone_number:
                    return RedirectResponse(url="/update-phone", status_code=303)
                
                # 🚨 렌더링 에러 방지 (로그가 없어도 안전하게 처리)
                logs_query = db.query(CreditLog).filter(CreditLog.student_id == user.student_id).order_by(CreditLog.created_at.desc())
                logs = logs_query.all() if logs_query else []
                
                return templates.TemplateResponse(request=request, name="index.html", context={"request": request, "user": user, "logs": logs, "notice": system_notice, "event_state": event_state})
        except: pass
    return templates.TemplateResponse(request=request, name="login.html", context={"request": request, "notice": system_notice, "event_state": event_state})

@app.post("/")
async def process_login(response: Response, request: Request, student_id: str = Form(...), password: str = Form(...), db: Session = Depends(get_db)):
    user = db.query(Student).filter(Student.student_id == student_id).first()
    
    # 🚨 최종 보스 검거: .encode('utf-8')[:72] 처리 (바이트 에러 차단)
    if not user or not user.is_verified or not pwd_context.verify(password.encode('utf-8')[:72], user.password_hash):
        return templates.TemplateResponse(request=request, name="login.html", context={"request": request, "error": "학번 또는 비밀번호가 틀렸습니다.", "notice": system_notice, "event_state": event_state})
    
    token = serializer.dumps({"student_id": user.student_id})
    res = RedirectResponse(url="/", status_code=303)
    res.set_cookie(key="user_session", value=token, httponly=True, max_age=1800)
    return res

@app.get("/logout")
async def logout():
    res = RedirectResponse(url="/", status_code=302)
    res.delete_cookie("user_session")
    res.delete_cookie("admin_session")
    return res

@app.get("/update-phone", response_class=HTMLResponse)
async def update_phone_page(request: Request, user_session: str = Cookie(None), db: Session = Depends(get_db)):
    if not user_session: return RedirectResponse(url="/")
    try:
        data = serializer.loads(user_session)
        user = db.query(Student).filter(Student.student_id == data["student_id"]).first()
        if not user: return RedirectResponse(url="/logout")
        if user.phone_number: return RedirectResponse(url="/")
        return templates.TemplateResponse(request=request, name="update_phone.html", context={"request": request, "notice": system_notice, "event_state": event_state})
    except: return RedirectResponse(url="/logout")

@app.post("/update-phone")
async def process_update_phone(phone_number: str = Form(...), user_session: str = Cookie(None), db: Session = Depends(get_db)):
    if not user_session: return RedirectResponse(url="/")
    try:
        data = serializer.loads(user_session)
        user = db.query(Student).filter(Student.student_id == data["student_id"]).first()
        if user:
            user.phone_number = phone_number
            db.commit()
    except: pass
    return RedirectResponse(url="/", status_code=303)

@app.post("/claim-event")
async def claim_event(user_session: str = Cookie(None), db: Session = Depends(get_db)):
    if not event_state["is_active"] or not user_session: return RedirectResponse(url="/")
    try:
        data = serializer.loads(user_session)
        user = db.query(Student).filter(Student.student_id == data["student_id"]).first()
        if user:
            exists = db.query(CreditLog).filter(CreditLog.student_id == user.student_id, CreditLog.event_name == f"[현장] {event_state['name']}").first()
            if not exists:
                user.total_credits += event_state["amount"]
                db.add(CreditLog(student_id=user.student_id, event_name=f"[현장] {event_state['name']}", amount=event_state["amount"]))
                db.commit()
    except: pass
    return RedirectResponse(url="/", status_code=303)

@app.get("/signup", response_class=HTMLResponse)
async def signup_page(request: Request):
    return templates.TemplateResponse(request=request, name="signup.html", context={"request": request, "notice": system_notice, "event_state": event_state})

@app.post("/signup")
async def process_signup(
    request: Request, student_id: str = Form(...), name: str = Form(...), 
    department: str = Form(...), grade: int = Form(...), phone_number: str = Form(...), 
    password: str = Form(...), db: Session = Depends(get_db)
):
    existing_user = db.query(Student).filter(Student.student_id == student_id).first()
    if existing_user: 
        return templates.TemplateResponse(request=request, name="signup.html", context={"request": request, "error": "이미 가입된 학번입니다.", "notice": system_notice, "event_state": event_state})
    
    # 🚨 .encode('utf-8')[:72] 추가
    new_user = Student(
        student_id=student_id, name=name, department=department, grade=grade,
        phone_number=phone_number, password_hash=pwd_context.hash(password.encode('utf-8')[:72]), is_verified=True, total_credits=0
    )
    db.add(new_user)
    db.commit()
    return templates.TemplateResponse(request=request, name="login.html", context={"request": request, "message": "가입이 완료되었습니다! 로그인해주세요.", "notice": system_notice, "event_state": event_state})

@app.get("/forgot-password", response_class=HTMLResponse)
async def forgot_pw_page(request: Request):
    return templates.TemplateResponse(request=request, name="forgot_password.html", context={"request": request, "notice": system_notice, "event_state": event_state})

@app.post("/forgot-password")
async def process_forgot_pw(request: Request, student_id: str = Form(...), name: str = Form(...), new_password: str = Form(...), db: Session = Depends(get_db)):
    user = db.query(Student).filter(Student.student_id == student_id, Student.name == name).first()
    if not user or not user.is_verified: 
        return templates.TemplateResponse(request=request, name="forgot_password.html", context={"request": request, "error": "정보가 일치하지 않습니다.", "notice": system_notice, "event_state": event_state})
        
    # 🚨 .encode('utf-8')[:72] 추가
    user.password_hash = pwd_context.hash(new_password.encode('utf-8')[:72])
    db.commit()
    
    # 🚨 렌더링 에러 방지: 템플릿 반환 대신 깔끔하게 리다이렉트
    return RedirectResponse(url="/", status_code=303)

@app.get("/ranking", response_class=HTMLResponse)
async def ranking_page(request: Request, db: Session = Depends(get_db)):
    students = db.query(Student).filter(Student.is_verified == True).order_by(Student.total_credits.desc()).all()
    ranking = [{"rank": i+1, "name": s.name[0]+"*"+s.name[-1] if len(s.name)>2 else s.name[0]+"*", "sid": s.student_id[:2]+"****"+s.student_id[-2:], "dept": s.department, "credits": s.total_credits} for i, s in enumerate(students)]
    return templates.TemplateResponse(request=request, name="ranking.html", context={"request": request, "ranking": ranking})

# --- 관리자 로직 ---
@app.get("/admin/login", response_class=HTMLResponse)
async def admin_login_page(request: Request):
    return templates.TemplateResponse(request=request, name="admin_login.html", context={"request": request})

@app.post("/admin/login")
async def process_admin_login(request: Request, student_id: str = Form(...), password: str = Form(...), db: Session = Depends(get_db)):
    if student_id not in ADMIN_STUDENTS: return templates.TemplateResponse(request=request, name="admin_login.html", context={"request": request, "error": "관리자 권한이 없습니다."})
    user = db.query(Student).filter(Student.student_id == student_id).first()
    
    # 🚨 관리자 로그인도 바이트 에러 방지
    if not user or not pwd_context.verify(password.encode('utf-8')[:72], user.password_hash):
        return templates.TemplateResponse(request=request, name="admin_login.html", context={"request": request, "error": "비밀번호가 틀렸습니다."})
    
    token = serializer.dumps({"admin_id": student_id})
    res = RedirectResponse(url="/admin/credit", status_code=302)
    res.set_cookie(key="admin_session", value=token, httponly=True)
    return res

@app.get("/admin/credit", response_class=HTMLResponse)
async def admin_page(request: Request, admin_session: str = Cookie(None)):
    if not admin_session: return RedirectResponse(url="/admin/login")
    return templates.TemplateResponse(request=request, name="admin_credit.html", context={"request": request, "notice": system_notice, "event_state": event_state})

@app.post("/admin/toggle-event")
async def toggle_event(action: str = Form(...), event_name: str = Form(""), amount: int = Form(0), admin_session: str = Cookie(None)):
    if not admin_session: return RedirectResponse(url="/admin/login")
    if action == "open":
        event_state.update({"is_active": True, "name": event_name, "amount": amount})
    else:
        event_state.update({"is_active": False, "name": "", "amount": 0})
    return RedirectResponse(url="/admin/credit", status_code=303)

@app.post("/admin/credit")
async def give_credit(request: Request, event_name: str = Form(...), amount: int = Form(...), student_ids_raw: str = Form(...), db: Session = Depends(get_db), admin_session: str = Cookie(None)):
    if not admin_session: return RedirectResponse(url="/admin/login")
    sids = [s.strip() for s in student_ids_raw.split('\n') if s.strip()]
    count = 0
    for sid in sids:
        u = db.query(Student).filter(Student.student_id == sid).first()
        if u:
            u.total_credits += amount
            db.add(CreditLog(student_id=sid, event_name=event_name, amount=amount))
            count += 1
    db.commit()
    return templates.TemplateResponse(request=request, name="admin_credit.html", context={"request": request, "message": f"{count}명 지급 완료!", "notice": system_notice, "event_state": event_state})

@app.post("/admin/update-notice")
async def update_notice(notice_text: str = Form(...), admin_session: str = Cookie(None)):
    global system_notice
    if not admin_session: return RedirectResponse(url="/admin/login")
    system_notice = notice_text
    return RedirectResponse(url="/admin/credit", status_code=303)

@app.get("/admin/ranking", response_class=HTMLResponse)
async def admin_ranking_page(request: Request, db: Session = Depends(get_db), admin_session: str = Cookie(None)):
    if not admin_session: return RedirectResponse(url="/admin/login")
    students = db.query(Student).filter(Student.is_verified == True).order_by(Student.total_credits.desc()).all()
    ranking = [{"rank": i+1, "sid": s.student_id, "name": s.name, "dept": s.department, "credits": s.total_credits, "phone": s.phone_number} for i, s in enumerate(students)]
    return templates.TemplateResponse(request=request, name="admin_ranking.html", context={"request": request, "ranking": ranking})

@app.get("/admin/student/{student_id}", response_class=HTMLResponse)
async def admin_student_detail(request: Request, student_id: str, db: Session = Depends(get_db), admin_session: str = Cookie(None)):
    if not admin_session: return RedirectResponse(url="/admin/login")
    user = db.query(Student).filter(Student.student_id == student_id).first()
    if not user: return RedirectResponse(url="/admin/ranking")
    
    logs_query = db.query(CreditLog).filter(CreditLog.student_id == student_id).order_by(CreditLog.created_at.desc())
    logs = logs_query.all() if logs_query else []
    
    return templates.TemplateResponse(request=request, name="admin_student_detail.html", context={"request": request, "user": user, "logs": logs})

@app.get("/admin/download-ranking")
async def download_ranking(admin_session: str = Cookie(None), db: Session = Depends(get_db)):
    if not admin_session: return RedirectResponse(url="/admin/login")
    students = db.query(Student).filter(Student.is_verified == True).order_by(Student.total_credits.desc()).all()
    output = io.StringIO()
    output.write('\ufeff')
    writer = csv.writer(output)
    writer.writerow(["순위", "학번", "이름", "학과", "학년", "보유 크레딧", "전화번호"])
    for i, s in enumerate(students):
        writer.writerow([i + 1, s.student_id, s.name, s.department, s.grade, s.total_credits, s.phone_number or "미입력"])
    output.seek(0)
    return StreamingResponse(io.StringIO(output.read()), media_type="text/csv", headers={"Content-Disposition": "attachment; filename=dku_credit_ranking.csv"})

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)

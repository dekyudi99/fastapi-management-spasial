from sqlalchemy.orm import Session
from models.logs import Logs
from typing import Optional, Any, Dict

def create_log(
    db: Session,
    auth_type: str,  # "API_KEY" atau "JWT"
    action: str,     # contoh: "S2S_PUBLISH_URL", "S2S_PUBLISH_FILE", "S2S_LIST_LAYERS"
    resource_type: str,  # contoh: "LAYER", "LAYER_GROUP", "WORKSPACE"
    resource_id: Optional[str] = None,
    resource_name: Optional[str] = None,
    status: str = "SUCCESS",  # "SUCCESS" atau "FAILED"
    user_id: Optional[int] = None,
    api_key_id: Optional[int] = None,
    project_id: Optional[int] = None,
    client_user_id: Optional[str] = None,
    client_user_email: Optional[str] = None,
    client_user_name: Optional[str] = None,
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None,
    meta_data: Optional[Dict[str, Any]] = None,
) -> Optional[Logs]:
    """
    Helper untuk mencatat aktivitas ke tabel logs secara terstruktur.
    Aman dari crash (jika logging gagal, tidak membatalkan transaksi utama).
    """
    try:
        log_entry = Logs(
            auth_type=auth_type,
            user_id=user_id,
            api_key_id=api_key_id,
            project_id=project_id,
            client_user_id=str(client_user_id) if client_user_id is not None else None,
            client_user_email=client_user_email,
            client_user_name=client_user_name,
            action=action,
            resource_type=resource_type,
            resource_id=str(resource_id) if resource_id is not None else None,
            resource_name=resource_name,
            status=status,
            ip_address=ip_address,
            user_agent=user_agent,
            meta_data=meta_data,
        )
        db.add(log_entry)
        db.commit()
        return log_entry
    except Exception as e:
        print(f"[LogService] Gagal menulis log: {e}")
        try:
            db.rollback()
        except Exception:
            pass
        return None

import warnings
# Suppress warnings
warnings.filterwarnings("ignore")
import os
import json
import re
import sys
import requests
from datetime import datetime
from typing import List, Tuple
from fastapi import APIRouter, UploadFile, File, Form, HTTPException
from fastapi.responses import JSONResponse
from bson import ObjectId
from dotenv import load_dotenv
from pydantic import BaseModel

try:
    from PIL import Image
    import numpy as np
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False
    print("[INIT] PIL/Pillow not available - image quality validation disabled")

from db.mongo import sync_prescriptions, sync_schedules, sync_users
from prescription.enrichment import enrich_medicines, parse_prescription_with_groq

load_dotenv()

# Set OCR.space API key from environment
OCR_SPACE_API_KEY = os.getenv("OCR_SPACE_API_KEY", "K82908764288957")
print(f"[INIT] OCR.space API initialized (Key: {OCR_SPACE_API_KEY[:10]}...)")

router = APIRouter()

# ==== PYDANTIC MODELS ====
class MedicineSchedule(BaseModel):
    prescription_id: str
    medicine_name: str
    dosage: str
    frequency: str
    timings: List[str]
    enabled: bool = True

class ScheduleToggle(BaseModel):
    schedule_id: str
    enabled: bool

class ScheduleUpdate(BaseModel):
    medicine_name: str = None
    dosage: str = None
    frequency: str = None
    timings: List[str] = None

# ==== HELPERS ====
def serialize_doc(doc):
    """Convert ObjectId and datetime fields to str for JSON response"""
    if not doc:
        return doc
    if "_id" in doc:
        doc["_id"] = str(doc["_id"])
    for key, value in doc.items():
        if isinstance(value, datetime):
            doc[key] = value.isoformat()
    return doc

def validate_image_quality(image_path: str) -> Tuple[bool, str, dict]:
    """Validate image quality before OCR processing"""
    if not PIL_AVAILABLE:
        return True, "Quality check skipped (PIL not available)", {}
    
    try:
        img = Image.open(image_path)
        
        # Convert to RGB if needed
        if img.mode != 'RGB':
            img = img.convert('RGB')
        
        width, height = img.size
        file_size = os.path.getsize(image_path)
        
        quality_metrics = {
            "width": width,
            "height": height,
            "file_size_kb": round(file_size / 1024, 2),
            "aspect_ratio": round(width / height, 2) if height > 0 else 0
        }
        
        warnings = []
        
        # Check 1: Minimum resolution
        min_dimension = min(width, height)
        if min_dimension < 600:
            warnings.append(f"Low resolution ({width}x{height}). Recommended minimum: 600px. OCR accuracy may be affected.")
        
        # Check 2: Very small file size (might indicate high compression)
        if file_size < 50 * 1024:  # Less than 50KB
            warnings.append(f"Small file size ({quality_metrics['file_size_kb']}KB). Image may be heavily compressed.")
        
        # Check 3: Extreme aspect ratios
        aspect_ratio = width / height if height > 0 else 0
        if aspect_ratio > 3 or aspect_ratio < 0.3:
            warnings.append(f"Unusual aspect ratio ({quality_metrics['aspect_ratio']}). Image may be cropped or distorted.")
        
        # Check 4: Basic brightness check (if image is too dark or too bright)
        try:
            img_array = np.array(img)
            mean_brightness = np.mean(img_array)
            quality_metrics["brightness"] = round(float(mean_brightness), 2)
            
            if mean_brightness < 50:
                warnings.append(f"Image appears very dark (brightness: {quality_metrics['brightness']}). Better lighting recommended.")
            elif mean_brightness > 220:
                warnings.append(f"Image appears overexposed (brightness: {quality_metrics['brightness']}). Reduce brightness.")
        except:
            pass  # Skip brightness check if numpy/conversion fails
        
        if warnings:
            warning_message = " ".join(warnings)
            return False, warning_message, quality_metrics
        
        return True, "Image quality acceptable", quality_metrics
        
    except Exception as e:
        print(f"[QUALITY CHECK] Error validating image: {e}")
        return True, f"Quality check failed: {str(e)}", {}

def extract_text_from_image_with_ocrspace(image_path: str) -> str:
    """Extract text from image using OCR.space API"""
    if not OCR_SPACE_API_KEY:
        raise HTTPException(status_code=500, detail="OCR_SPACE_API_KEY not configured in environment")
    
    try:
        print(f"[OCR.space] Starting OCR for: {image_path}")
        sys.stdout.flush()
        
        # Prepare the request
        url = "https://api.ocr.space/parse/image"
        
        with open(image_path, 'rb') as f:
            files = {
                'file': (os.path.basename(image_path), f, 'image/jpeg')
            }
            
            payload = {
                'apikey': OCR_SPACE_API_KEY,
                'language': 'eng',
                'isOverlayRequired': 'false',
                'OCREngine': '2',  # Engine 2 is better for special characters
                'scale': 'true',   # Improve OCR for low-res images
                'isTable': 'false'
            }
            
            print(f"[OCR.space] Sending request to API...")
            sys.stdout.flush()
            response = requests.post(url, files=files, data=payload, timeout=30)
        
        print(f"[OCR.space] Response status: {response.status_code}")
        sys.stdout.flush()
        
        if response.status_code != 200:
            raise Exception(f"OCR.space API returned status {response.status_code}")
        
        result = response.json()
        print(f"[OCR.space] Response received")
        sys.stdout.flush()
        
        # Check for errors
        if result.get('IsErroredOnProcessing', False):
            error_msg = result.get('ErrorMessage', 'Unknown error')
            error_details = result.get('ErrorDetails', '')
            raise Exception(f"OCR.space processing error: {error_msg} - {error_details}")
        
        # Extract text from parsed results
        parsed_results = result.get('ParsedResults', [])
        if not parsed_results:
            raise Exception("No parsed results returned from OCR.space")
        
        extracted_text = ""
        for page_result in parsed_results:
            exit_code = page_result.get('FileParseExitCode')
            
            if exit_code == 1:  # Success
                text = page_result.get('ParsedText', '')
                extracted_text += text + "\n"
            else:
                error_msg = page_result.get('ErrorMessage', 'Parse failed')
                print(f"[OCR.space] Warning: Page parse failed - {error_msg}")
        
        if not extracted_text.strip():
            raise Exception("No text extracted from image")
        
        print(f"[OCR.space] Successfully extracted {len(extracted_text)} characters")
        sys.stdout.flush()
        return extracted_text
    
    except Exception as e:
        print(f"[OCR.space] Error: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"OCR failed: {str(e)}")


# ==== ROUTES ====

@router.post("/upload-prescription")
async def upload_prescription(file: UploadFile = File(...), user_id: str = Form(...)):
    """Upload prescription and create medicine schedule using OCR.space API"""
    print(f"[UPLOAD] ========== NEW UPLOAD REQUEST ==========")
    sys.stdout.flush()
    print(f"[UPLOAD] User ID: {user_id}")
    print(f"[UPLOAD] File: {file.filename}, Content-Type: {file.content_type}")
    sys.stdout.flush()
    
    try:
        # Verify user exists
        print(f"[UPLOAD] Verifying user exists...")
        user = sync_users.find_one({"_id": ObjectId(user_id)})
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        print(f"[UPLOAD] User verified: {user.get('email', 'N/A')}")
        
        # Save uploaded file temporarily
        file_location = f"temp_{file.filename}"
        print(f"[UPLOAD] Saving file to: {file_location}")
        with open(file_location, "wb") as f:
            f.write(await file.read())
        print(f"[UPLOAD] File saved successfully")

        # Validate image quality before OCR
        print(f"[UPLOAD] Validating image quality...")
        quality_valid, quality_message, quality_metrics = validate_image_quality(file_location)
        quality_warnings = []
        
        if not quality_valid:
            quality_warnings.append(quality_message)
            print(f"[UPLOAD] Quality warning: {quality_message}")

        # Extract text using OCR.space API
        print(f"[UPLOAD] Starting OCR extraction...")
        sys.stdout.flush()
        text = extract_text_from_image_with_ocrspace(file_location)
        print(f"[OCR] Extracted {len(text)} characters")

        # Parse prescription using Groq LLM
        medicines = parse_prescription_with_groq(text)
        print(f"[PARSE] Found {len(medicines)} medicines")

        # Enrich with LLM + web search
        enriched_medicines, enrichment_stats = enrich_medicines(medicines)
        print(f"[ENRICHMENT] {enrichment_stats['enriched_count']} enriched, {enrichment_stats['skipped_count']} complete")
        
        # Use enriched medicines for storage and scheduling
        medicines = enriched_medicines

        # Convert to JSON string for storage
        structured_json = json.dumps(medicines)

        # Save prescription
        prescription_doc = {
            "user_id": user_id,
            "raw_text": text,
            "structured_data": structured_json,
            "created_at": datetime.utcnow()
        }
        prescription_id = sync_prescriptions.insert_one(prescription_doc).inserted_id

        # Create schedules
        schedule_ids = []
        valid_timings = ["morning", "afternoon", "evening", "night"]
        
        for medicine in medicines:
            if isinstance(medicine, dict):
                medicine_name = medicine.get("medicine_name", "N/A")
                timings = medicine.get("timings", [])
                
                # Skip invalid medicines
                if not medicine_name or medicine_name in ["N/A", "Unknown", "Unknown Medicine"]:
                    continue
                
                # Ensure timings are valid
                if not timings or not isinstance(timings, list):
                    timings = ["morning"]
                else:
                    timings = [t for t in timings if t in valid_timings]
                    if not timings:
                        timings = ["morning"]
                
                schedule_doc = {
                    "user_id": user_id,
                    "prescription_id": str(prescription_id),
                    "medicine_name": medicine_name,
                    "dosage": medicine.get("dosage", "N/A"),
                    "frequency": medicine.get("frequency", "N/A"),
                    "timings": timings,
                    "enabled": True,
                    "created_at": datetime.utcnow(),
                    "last_reminder_sent": None
                }
                schedule_id = sync_schedules.insert_one(schedule_doc).inserted_id
                schedule_ids.append(str(schedule_id))

        # Clean up temp file
        try:
            os.remove(file_location)
        except:
            pass

        # Check if no medicines were extracted
        if not medicines or len(schedule_ids) == 0:
            error_response = {
                "success": False,
                "prescription_id": str(prescription_id),
                "schedule_ids": [],
                "medicines": [],
                "message": "No medicines detected. This may be due to poor image quality, unclear text, or non-standard prescription format. Please try uploading a clearer image or contact support.",
                "raw_text_preview": text[:300] if text else "No text extracted",
                "suggestions": [
                    "Ensure the image is clear and well-lit",
                    "Make sure the prescription text is readable",
                    "Try taking the photo straight-on (not at an angle)",
                    "Check that medicine names and dosages are visible"
                ]
            }
            
            # Add quality warnings if any
            if quality_warnings:
                error_response["quality_warnings"] = quality_warnings
                error_response["quality_metrics"] = quality_metrics
            
            return JSONResponse(error_response, status_code=400)
        
        # Build success message with warnings if any
        message = f"Prescription uploaded successfully. {len(medicines)} medicine(s) extracted and {len(schedule_ids)} schedule(s) created."
        if len(medicines) != len(schedule_ids):
            message += f" Note: Some medicines were skipped (e.g., 'as needed' medications)."
        
        # Add enrichment information to message
        if enrichment_stats.get("enriched_count", 0) > 0:
            message += f" {enrichment_stats['enriched_count']} medicine(s) enhanced with AI-powered information."

        response_data = {
            "success": True,
            "prescription_id": str(prescription_id),
            "schedule_ids": schedule_ids,
            "medicines": medicines,
            "message": message,
            "schedules_created": len(schedule_ids),
            "medicines_detected": len(medicines),
            "enrichment_stats": enrichment_stats
        }
        
        # Add quality warnings if any
        if quality_warnings:
            response_data["quality_warnings"] = quality_warnings
            response_data["quality_metrics"] = quality_metrics

        return JSONResponse(response_data)

    except HTTPException:
        raise
    except Exception as e:
        print(f"Error in upload_prescription: {str(e)}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/user/{user_id}/schedules")
async def get_user_schedules(user_id: str):
    """Get all schedules for a user"""
    try:
        user_schedules = list(sync_schedules.find({"user_id": user_id}))
        return [serialize_doc(schedule) for schedule in user_schedules]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/user/{user_id}/prescriptions")
async def get_user_prescriptions(user_id: str):
    """Get all prescriptions for a user"""
    try:
        user_prescriptions = list(sync_prescriptions.find({"user_id": user_id}))
        return [serialize_doc(prescription) for prescription in user_prescriptions]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/toggle-schedule")
async def toggle_schedule(toggle_data: ScheduleToggle):
    """Enable or disable a specific schedule"""
    try:
        result = sync_schedules.update_one(
            {"_id": ObjectId(toggle_data.schedule_id)},
            {"$set": {"enabled": toggle_data.enabled}}
        )
        
        if result.matched_count == 0:
            raise HTTPException(status_code=404, detail="Schedule not found")
        
        status = "enabled" if toggle_data.enabled else "disabled"
        return JSONResponse({
            "success": True,
            "message": f"Schedule {status} successfully"
        })
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.delete("/schedule/{schedule_id}")
async def delete_schedule(schedule_id: str):
    """Delete a specific schedule"""
    try:
        result = sync_schedules.delete_one({"_id": ObjectId(schedule_id)})
        
        if result.deleted_count == 0:
            raise HTTPException(status_code=404, detail="Schedule not found")
        
        return JSONResponse({
            "success": True,
            "message": "Schedule deleted successfully"
        })
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/schedule/{schedule_id}")
async def update_schedule(schedule_id: str, update_data: ScheduleUpdate):
    """Update a specific schedule's fields"""
    try:
        # Build update dict from provided (non-None) fields
        update_fields = {}
        
        if update_data.medicine_name is not None:
            update_fields["medicine_name"] = update_data.medicine_name.strip()
        
        if update_data.dosage is not None:
            update_fields["dosage"] = update_data.dosage.strip()
        
        if update_data.frequency is not None:
            update_fields["frequency"] = update_data.frequency.strip()
        
        if update_data.timings is not None:
            valid_timings = ["morning", "afternoon", "evening", "night"]
            cleaned_timings = [t for t in update_data.timings if t in valid_timings]
            if not cleaned_timings:
                raise HTTPException(
                    status_code=400,
                    detail="At least one valid timing is required (morning, afternoon, evening, night)"
                )
            update_fields["timings"] = cleaned_timings
        
        if not update_fields:
            raise HTTPException(status_code=400, detail="No fields to update")
        
        update_fields["updated_at"] = datetime.utcnow()
        
        result = sync_schedules.update_one(
            {"_id": ObjectId(schedule_id)},
            {"$set": update_fields}
        )
        
        if result.matched_count == 0:
            raise HTTPException(status_code=404, detail="Schedule not found")
        
        # Return the updated document
        updated_doc = sync_schedules.find_one({"_id": ObjectId(schedule_id)})
        
        return JSONResponse({
            "success": True,
            "message": "Schedule updated successfully",
            "schedule": serialize_doc(updated_doc)
        })
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


import cv2
import mediapipe as mp
import math
import time

# --- Mediapipe Setup ---
mp_face_mesh = mp.solutions.face_mesh
mp_drawing = mp.solutions.drawing_utils

# Indices for eyes and mouth landmarks (Mediapipe face mesh)
LEFT_EYE = [33, 160, 158, 133, 153, 144]  # Approx. left eye region
RIGHT_EYE = [362, 385, 387, 263, 373, 380]
UPPER_LIP = [13, 14]  # Approx. upper/lower lip center
LOWER_LIP = [14, 17]

# EAR threshold (lower = eyes closed)
EYE_AR_THRESH = 0.2
EYE_AR_CONSEC_FRAMES = 15  # Number of frames to confirm drowsiness
MOUTH_OPEN_THRESH = 0.65  # Relative openness threshold for yawning

# --- Helper Functions ---
def euclidean_distance(p1, p2):
    return math.dist(p1, p2)

def eye_aspect_ratio(landmarks, eye_indices, w, h):
    points = [(int(landmarks[i].x * w), int(landmarks[i].y * h)) for i in eye_indices]
    # vertical distances
    A = euclidean_distance(points[1], points[5])
    B = euclidean_distance(points[2], points[4])
    # horizontal distance
    C = euclidean_distance(points[0], points[3])
    ear = (A + B) / (2.0 * C)
    return ear

def mouth_open_ratio(landmarks, w, h):
    upper_lip_y = landmarks[UPPER_LIP[0]].y * h
    lower_lip_y = landmarks[LOWER_LIP[1]].y * h
    mouth_height = abs(lower_lip_y - upper_lip_y)
    face_height = h  # normalize by frame height
    return mouth_height / face_height

# --- Main Loop ---
cap = cv2.VideoCapture(0)
blink_counter = 0
yawn_detected = False
last_yawn_time = 0

with mp_face_mesh.FaceMesh(
    max_num_faces=1,
    refine_landmarks=True,
    min_detection_confidence=0.5,
    min_tracking_confidence=0.5
) as face_mesh:

    while cap.isOpened():
        success, frame = cap.read()
        if not success:
            break

        h, w, _ = frame.shape
        rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        results = face_mesh.process(rgb_frame)

        if results.multi_face_landmarks:
            for face_landmarks in results.multi_face_landmarks:
                # Calculate EAR for both eyes
                left_ear = eye_aspect_ratio(face_landmarks.landmark, LEFT_EYE, w, h)
                right_ear = eye_aspect_ratio(face_landmarks.landmark, RIGHT_EYE, w, h)
                ear = (left_ear + right_ear) / 2.0

                # Detect closed eyes (drowsiness)
                if ear < EYE_AR_THRESH:
                    blink_counter += 1
                else:
                    blink_counter = 0

                if blink_counter >= EYE_AR_CONSEC_FRAMES:
                    cv2.putText(frame, "😴 DROWSINESS DETECTED", (50, 100),
                                cv2.FONT_HERSHEY_SIMPLEX, 1.2, (0, 0, 255), 3)
                    #print("sleepy")

                # Detect yawning
                mouth_ratio = mouth_open_ratio(face_landmarks.landmark, w, h)
                if mouth_ratio > MOUTH_OPEN_THRESH and (time.time() - last_yawn_time) > 3:
                    yawn_detected = True
                    last_yawn_time = time.time()

                if yawn_detected:
                    cv2.putText(frame, "😮 YAWN DETECTED", (50, 150),
                                cv2.FONT_HERSHEY_SIMPLEX, 1.2, (0, 255, 255), 3)
                    #print("sleepy")
                    # reset after 2 seconds
                    if time.time() - last_yawn_time > 2:
                        yawn_detected = False

                # Optional: draw face mesh
                mp_drawing.draw_landmarks(
                    frame,
                    face_landmarks,
                    mp_face_mesh.FACEMESH_TESSELATION,
                    mp_drawing.DrawingSpec(color=(0,255,0), thickness=1, circle_radius=1)
                )

        cv2.imshow('Driver Monitor', frame)
        if cv2.waitKey(5) & 0xFF == 27:
            break

cap.release()
cv2.destroyAllWindows()

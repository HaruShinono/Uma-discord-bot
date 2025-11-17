# Cơ Chế Hoạt Động Của Hệ Thống Đua Ngựa Shinono

Tài liệu này giải thích chi tiết về logic và các thuật toán đằng sau hệ thống đua ngựa của bot, bao gồm chỉ số ngựa, cách tính toán di chuyển, các loại cuộc đua và các sự kiện đặc biệt.

## 1. Chỉ Số Ngựa (Horse Stats)

Mỗi con ngựa sở hữu 5 chỉ số cốt lõi, được khởi tạo ngẫu nhiên khi cuộc đua bắt đầu.

-   **Tốc Độ (Speed)**: Ảnh hưởng chính đến tốc độ di chuyển cơ bản.
-   **Sức Mạnh (Power)**: Ảnh hưởng đến khả năng bứt tốc.
-   **Thể Lực (Stamina)**: Giúp duy trì phong độ, giảm nguy cơ kiệt sức.
-   **Nhanh Nhẹn (Agility)**: Giảm khả năng bị vấp ngã.
-   **Tập Trung (Focus)**: Giúp ngựa giữ vững tinh thần và tốc độ.

Các chỉ số này được tạo ngẫu nhiên trong một khoảng xác định:

```python
STAT_MIN = 200
STAT_MAX = 1200

def init_stats() -> Dict[str, int]:
    return {
        "speed": random.randint(STAT_MIN, STAT_MAX),
        "power": random.randint(STAT_MIN, STAT_MAX),
        "stamina": random.randint(STAT_MIN, STAT_MAX),
        "agility": random.randint(STAT_MIN, STAT_MAX),
        "focus": random.randint(STAT_MIN, STAT_MAX)
    }
```

## 2. Logic Vòng Lặp Cuộc Đua (Race Loop)

Cuộc đua diễn ra theo từng "tick", mỗi tick tương ứng với một khoảng thời gian thực.

```python
TICK_SECONDS = 1 # Mỗi tick cách nhau 1 giây
```

Trong mỗi tick, bot sẽ thực hiện các hành động sau cho từng ngựa:

1.  **Kiểm tra trạng thái**: Bỏ qua nếu ngựa đã về đích hoặc đang bị phạt do vấp ngã.
2.  **Tính toán di chuyển**: Xác định quãng đường ngựa đi được trong tick đó.
3.  **Cập nhật vị trí**: Cộng quãng đường vừa tính vào vị trí hiện tại.
4.  **Kiểm tra về đích**: Nếu vị trí vượt qua tổng quãng đường, đánh dấu ngựa đã hoàn thành.

### 2.1. Công Thức Tính Toán Di Chuyển

Quãng đường di chuyển trong mỗi tick được tính bằng tổng của hai thành phần: **di chuyển ngẫu nhiên** và **thưởng từ chỉ số**.

```
move = random.randint(1, 6) + stats_bonus
```

Trong đó, `stats_bonus` được tính toán dựa trên trọng số của 5 chỉ số:

```python
def stats_to_move_bonus(stats: Dict[str, int], horse_name: str, is_special_race: bool) -> float:
    # (Bỏ qua logic cho ngựa đặc biệt như Sonic, Vedal)
    
    # Tổng hợp chỉ số có trọng số
    s = (stats["speed"] * 0.35 +
         stats["power"] * 0.3 +
         stats["stamina"] * 0.2 +
         stats["agility"] * 0.1 +
         stats["focus"] * 0.05)

    # Chuẩn hóa giá trị về một khoảng để tính thưởng
    s_min, s_max = float(STAT_MIN), float(STAT_MAX)
    frac = (s - s_min) / (s_max - s_min) if s_max != s_min else 0.0

    # Nhân với hệ số thưởng của loại đua
    # Đua đặc biệt thưởng nhiều hơn để cuộc đua nhanh hơn
    return frac * (20.0 if is_special_race else 4.0)
```

### 2.2. Biến Động Chỉ Số Giữa Cuộc Đua

Để tạo tính bất ngờ, chỉ số của ngựa sẽ thay đổi ngẫu nhiên sau một khoảng thời gian nhất định.

```python
STAT_UPDATE_SECONDS = 3 # Mỗi 3 giây, chỉ số sẽ được cập nhật
STAT_DELTA_MIN = 50
STAT_DELTA_MAX = 100
```

Cứ sau `3` giây, một chỉ số ngẫu nhiên của mỗi ngựa sẽ tăng hoặc giảm một lượng từ `50` đến `100`. Điều này tạo ra các "mood" (biểu cảm) khác nhau cho ngựa, ảnh hưởng đến hiệu suất của chúng.

### 2.3. Sự Kiện Ngẫu Nhiên

-   **Vấp Ngã (Fall)**: Ngựa có thể bị vấp ngã, khiến chúng bị phạt và không thể di chuyển trong vài tick. Tỷ lệ vấp ngã phụ thuộc vào quãng đường di chuyển trong một tick.
    ```python
    FALL_PENALTY_TICKS = 3 # Nghỉ 3 lượt (tick) nếu vấp ngã

    def check_fall(move: int) -> bool:
        if move >= 10: return random.random() < 0.06 # 6% nếu di chuyển >= 10m
        if move >= 8: return random.random() < 0.03  # 3% nếu di chuyển >= 8m
        return random.random() < 0.005              # 0.5% trong các trường hợp khác
    ```
-   **Kiệt Sức (Stamina Penalty)**: Nếu thể lực của ngựa xuống quá thấp (dưới 25% mức tối đa), chúng có 8% cơ hội bị giảm mạnh chỉ số và mất lượt.

## 3. Các Loại Cuộc Đua

Hệ thống có hai loại cuộc đua chính với luật chơi và phần thưởng khác nhau.

### 3.1. Đua Thường (`/umarace`)

-   **Số Lượng Tham Gia**: 8 ngựa (1 do người chơi chọn, 7 ngựa ngẫu nhiên từ danh sách).
-   **Khách Mời Đặc Biệt**: Có một tỷ lệ nhỏ các ngựa đặc biệt (như Sonic, Vedal) sẽ xuất hiện ngẫu nhiên trong cuộc đua thường.
    ```python
    # Tỷ lệ xuất hiện ngựa đặc biệt
    def determine_special_participants() -> List[Dict[str, str]]:
        roll = random.random()
        if roll < 0.002:  # 0.2% cho 3 ngựa
            # ...
        elif roll < 0.007: # 0.5% cho 2 ngựa
            # ...
        elif roll < 0.017: # 1% cho 1 ngựa
            # ...
    ```
-   **Cơ Chế Trả Thưởng**: Phần thưởng được tính bằng `số tiền cược * hệ số`.
    ```python
    PAYOUTS = {1: 3.5, 2: 2.5, 3: 2.0} # Hạng 1: x3.5, Hạng 2: x2.5, Hạng 3: x2.0
    ```

### 3.2. Đua Đặc Biệt (Sự kiện hàng ngày)

Đây là sự kiện lớn nhất trong ngày, được lên lịch tự động.

-   **Lịch Trình**:
    -   Mở cược lúc: `06:00 (UTC+7)`
    -   Bắt đầu đua lúc: `18:30 (UTC+7)`
-   **Điều kiện tham gia**: Chỉ **12 ngựa có lịch sử thắng nhiều nhất** được tham gia. Dữ liệu được lấy từ `horse_history.csv`.
-   **Cơ Chế Cược**:
    -   Mỗi người chỉ được cược **1 lần/ngày**.
    -   Mức cược tối thiểu cao hơn (`2000 xu`).
-   **Logic Hủy Đua**: Nếu thời gian hiện tại đã qua giờ bắt đầu đua (`18:30`) mà cuộc đua vẫn chưa diễn ra (do bot offline hoặc lỗi), cuộc đua sẽ bị hủy và đợi đến `00:00` ngày hôm sau để reset.
-   **Cơ Chế Trả Thưởng (Tiered Payouts)**: Phần thưởng được trả cho tất cả những người chơi đã cược đúng vào ngựa về Hạng 1, 2, hoặc 3.
    ```python
    SPECIAL_PAYOUTS = {1: 12.7, 2: 8.5, 3: 6.0} # Hạng 1: x12.7, Hạng 2: x8.5, Hạng 3: x6.0
    ```

## 4. Ngựa Đặc Biệt (Special Horses)

Những con ngựa này có logic di chuyển và chỉ số được lập trình sẵn, không tuân theo quy tắc ngẫu nhiên thông thường.

-   **Sonic**: Luôn di chuyển với tốc độ tối đa để về đích nhanh nhất có thể (gần như chắc chắn hạng 1).
-   **Vedal**: Luôn di chuyển với tốc độ tối thiểu là `1m` mỗi tick.

Chúng được định nghĩa trong `special_horses.csv` và có một hàm riêng để tính toán chỉ số/di chuyển.

```python
def get_special_horse_stats(special_type: str) -> Dict[str, int]:
    if special_type == "vedal":
        return {"speed": 1, "power": 1, "stamina": 1, "agility": 1, "focus": 1}
    if special_type == "sonic":
        return {"speed": 9999, "power": 9999, "stamina": 9999, "agility": 9999, "focus": 9999}
    return init_stats() # Mặc định
```
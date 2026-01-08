import streamlit as st
import streamlit.components.v1 as components
import folium
from streamlit_folium import folium_static
from pykml import parser
import os
from io import StringIO
import xml.etree.ElementTree as ET
import zipfile
from geopy.distance import geodesic
import json
import math
import pickle
import json
from datetime import datetime

def segment_to_dict(segment):
    """将 Segment 对象转换为字典"""
    return {
        'name': segment.name,
        'coordinates': segment.coordinates.copy(),  # 创建副本
        'elevations': segment.elevations.copy(),   # 创建副本
        'selected': segment.selected,
        'split_point_index': segment.split_point_index,
        'order': segment.order
    }

def dict_to_segment(data):
    """将字典转换为 Segment 对象"""
    segment = Segment(
        data['name'],
        data['coordinates'].copy(),  # 创建副本
        data['elevations'].copy(),   # 创建副本
        data['order']
    )
    segment.selected = data['selected']
    segment.split_point_index = data['split_point_index']
    return segment

def save_session_state(file_path):
    """保存会话状态到文件"""
    # 将 Segment 对象转换为字典
    segments_data = [segment_to_dict(segment) for segment in st.session_state.segments]
    
    # 创建要保存的数据字典
    save_data = {
        'segments': segments_data,
        'file_names': list(st.session_state.file_names),  # 转换set为list以便序列化
        'next_order': st.session_state.next_order,
        'next_segment_letter': st.session_state.next_segment_letter,
        'map_zoom': st.session_state.map_zoom if 'map_zoom' in st.session_state else 14,
        'map_center': st.session_state.map_center if 'map_center' in st.session_state else None,
        'has_uploaded': st.session_state.has_uploaded
    }
    
    # 保存到文件
    with open(file_path, 'wb') as f:
        pickle.dump(save_data, f)

def load_session_state(file_path):
    """从文件加载会话状态"""
    with open(file_path, 'rb') as f:
        save_data = pickle.load(f)
    
    # 将字典转换回 Segment 对象
    segments = [dict_to_segment(segment_data) for segment_data in save_data['segments']]
    
    # 恢复会话状态
    st.session_state.segments = segments
    st.session_state.file_names = set(save_data['file_names'])  # 转换回set
    st.session_state.next_order = save_data['next_order']
    st.session_state.next_segment_letter = save_data['next_segment_letter']
    st.session_state.map_zoom = save_data['map_zoom']
    st.session_state.map_center = save_data['map_center']
    st.session_state.has_uploaded = save_data['has_uploaded']

class Segment:
	def __init__(self, name, coordinates, elevations, order):
		self.name = name
		self.coordinates = coordinates
		self.elevations = elevations
		self.selected = False
		self.split_point_index = len(coordinates) // 2  # 默认在中间
		self.order = order
	
	def __repr__(self):
		return f"Segment({self.name}, {len(self.coordinates)} points, order={self.order})"

class SegmentManager:
	def __init__(self):
		if 'segments' not in st.session_state:
			st.session_state.segments = []
		if 'file_names' not in st.session_state:
			st.session_state.file_names = set()
		if 'next_order' not in st.session_state:
			st.session_state.next_order = 0
		if 'next_segment_letter' not in st.session_state:
			st.session_state.next_segment_letter = 'A'
	
	def update_segment_orders(self):
		"""更新所有段的顺序"""
		for i, segment in enumerate(st.session_state.segments):
			segment.order = i
		st.session_state.next_order = len(st.session_state.segments)
	
	def get_next_segment_name(self):
		"""获取下一个可用的段名称"""
		name = f"Segment {st.session_state.next_segment_letter}"
		# 更新下一个字母
		current = ord(st.session_state.next_segment_letter)
		next_letter = chr(current + 1)
		if next_letter > 'Z':  # 如果超过Z，从AA开始
			next_letter = 'AA'
		st.session_state.next_segment_letter = next_letter
		return name

	def add_segment(self, name, coordinates, elevations):
		# 检查文件是否已经加载
		if name not in st.session_state.file_names:
			segment_name = self.get_next_segment_name()
			segment = Segment(segment_name, coordinates, elevations, st.session_state.next_order)
			st.session_state.segments.append(segment)
			st.session_state.file_names.add(name)  # 仍然记录文件名以防重复上传
			self.update_segment_orders()

	def get_segments(self):
		# 按 order 排序返回
		return sorted(st.session_state.segments, key=lambda x: x.order)

	def clear_segments(self):
		st.session_state.segments = []
		st.session_state.file_names = set()
		st.session_state.next_order = 0

	def move_split_point(self, segment, direction, step=10):
		"""移动分裂点，每次移动 step 个点"""
		if direction == 'backward':
			# 向前移动 step 个点，但不超过起点
			new_index = max(0, segment.split_point_index - step)
			segment.split_point_index = new_index
		elif direction == 'forward':
			# 向后移动 step 个点，但不超过终点
			new_index = min(len(segment.coordinates) - 1, segment.split_point_index + step)
			segment.split_point_index = new_index
		elif direction == 'start_forward':
			# 起点向后移动 step 个点
			segment.coordinates = segment.coordinates[step:]
			segment.elevations = segment.elevations[step:]
			segment.split_point_index = max(0, segment.split_point_index - step)
		elif direction == 'end_backward':
			# 终点向前移动 step 个点
			segment.coordinates = segment.coordinates[:-step]
			segment.elevations = segment.elevations[:-step]
			segment.split_point_index = min(segment.split_point_index, len(segment.coordinates) - 1)
	
	def reverse_segment(self, segment):
		"""反转轨迹段的方向"""
		# 反转坐标和海拔数据
		segment.coordinates.reverse()
		segment.elevations.reverse()
		# 更新分裂点位置
		segment.split_point_index = len(segment.coordinates) - 1 - segment.split_point_index
	
	def delete_segment(self, segment):
		"""删除轨迹段"""
		# 从段列表中移除
		st.session_state.segments = [s for s in st.session_state.segments if s.order != segment.order]
		self.update_segment_orders()
	
	def duplicate_segment(self, segment):
		"""复制轨迹段"""
		# 创建新的坐标和海拔列表的副本
		new_coords = segment.coordinates.copy()
		new_elevs = segment.elevations.copy()
		
		# 创建新段
		new_segment = Segment(
			self.get_next_segment_name(),
			new_coords,
			new_elevs,
			st.session_state.next_order
		)
		
		# 复制分裂点位置
		new_segment.split_point_index = segment.split_point_index
		
		# 添加到段列表
		st.session_state.segments.append(new_segment)
		self.update_segment_orders()
		
		return new_segment

	def split_segment(self, segment):
		"""在分裂点处分割轨迹段"""
		# 创建两个新的轨迹段
		first_coords = segment.coordinates[:segment.split_point_index + 1]
		first_elevs = segment.elevations[:segment.split_point_index + 1]
		second_coords = segment.coordinates[segment.split_point_index:]
		second_elevs = segment.elevations[segment.split_point_index:]
		
		# 从段列表中移除原始段
		st.session_state.segments = [s for s in st.session_state.segments if s.order != segment.order]
		
		# 创建并添加新段
		first_segment = Segment(self.get_next_segment_name(), first_coords, first_elevs, 0)
		st.session_state.segments.append(first_segment)
		
		second_segment = Segment(self.get_next_segment_name(), second_coords, second_elevs, 1)
		st.session_state.segments.append(second_segment)
		
		# 更新所有段的顺序
		self.update_segment_orders()
		
		return first_segment, second_segment

	def move_segment(self, from_order, to_order):
		if 0 <= from_order < len(st.session_state.segments) and 0 <= to_order < len(st.session_state.segments):
			# 获取要移动的段
			segment_to_move = next(s for s in st.session_state.segments if s.order == from_order)
			
			# 从列表中移除该段
			st.session_state.segments.remove(segment_to_move)
			
			# 在新位置插入该段
			st.session_state.segments.insert(to_order, segment_to_move)
			
			# 更新所有段的顺序
			self.update_segment_orders()

def parse_kml(file):
	"""解析KML文件并提取坐标点"""
	coordinates = []
	elevations = []
	
	# 检查是否为KMZ文件
	if file.name.lower().endswith('.kmz'):
		# 创建临时文件来保存上传的内容
		with open("temp.kmz", "wb") as f:
			f.write(file.getvalue())
		
		# 解压KMZ文件
		with zipfile.ZipFile("temp.kmz", 'r') as zip_ref:
			kml_file = None
			for name in zip_ref.namelist():
				if name.lower().endswith('.kml'):
					kml_file = zip_ref.extract(name)
					break
			
			if kml_file is None:
				st.error("KMZ文件中未找到KML文件")
				return [], []
			
			with open(kml_file, 'r', encoding='utf-8') as f:
				content = f.read()
			
			# 清理临时文件
			os.remove(kml_file)
	else:
		# 直接读取KML文件
		content = file.getvalue().decode('utf-8')
	
	# 解析KML内容
	root = ET.fromstring(content)
	
	# 注册命名空间
	namespaces = {
		'gx': 'http://www.google.com/kml/ext/2.2',
		'kml': 'http://www.opengis.net/kml/2.2'
	}
	
	# 首先尝试查找 gx:Track 中的 gx:coord
	tracks = root.findall('.//gx:Track', namespaces)
	
	if tracks:
		# 如果找到 gx:Track，解析其中的 gx:coord
		for track in tracks:
			coords = track.findall('gx:coord', namespaces)
			for coord in coords:
				# gx:coord 格式为: "longitude latitude altitude"
				lon, lat, ele = coord.text.strip().split()
				coordinates.append([float(lat), float(lon)])
				elevations.append(float(ele))
	else:
		# 如果没有找到 gx:Track，尝试解析传统的 coordinates 标签
		for elem in root.iter('*'):
			if 'coordinates' in elem.tag:
				coords_text = elem.text.strip()
				coord_pairs = coords_text.split()
				
				for pair in coord_pairs:
					# coordinates 格式为: "longitude,latitude,altitude"
					lon, lat, ele = pair.split(',')
					coordinates.append([float(lat), float(lon)])
					elevations.append(float(ele))
	
	# 清理临时文件
	if file.name.lower().endswith('.kmz'):
		os.remove("temp.kmz")
	
	if not coordinates:
		st.warning("未找到任何轨迹点数据")
		return [], []
		
	return coordinates, elevations

def calculate_distance(coord1, coord2):
	"""计算两点之间的距离（米）"""
	return geodesic(coord1, coord2).meters

def export_to_kml(segments):
	"""将所有轨迹段导出为KML格式"""
	# 创建KML文档
	kml_str = """<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2"
	xmlns:gx="http://www.google.com/kml/ext/2.2">
	<Document>
		<name>导出的轨迹</name>
		<Style id="TbuluTrackStyle">
			<LineStyle>
				<color>ff0000ff</color>
				<width>3</width>
			</LineStyle>
			<LabelStyle>
				<scale>0.7</scale>
				<colorMode>normal</colorMode>
			</LabelStyle>
			<IconStyle>
				<scale>1.1</scale>
				<Icon>
					<href>http://www.2bulu.com/static/images/track_start.png</href>
				</Icon>
			</IconStyle>
		</Style>
		<Folder id="TbuluTrackFolder">
			<name>轨迹</name>
"""
	
	# 添加每个轨迹段
	for segment in sorted(segments, key=lambda x: x.order):
		# 计算统计信息
		total_distance = sum(calculate_distance(segment.coordinates[i], segment.coordinates[i+1])
						   for i in range(len(segment.coordinates)-1))
		max_elevation = max(segment.elevations)
		min_elevation = min(segment.elevations)
		
		# 计算累计爬升和下降
		elevation_changes = [segment.elevations[i+1] - segment.elevations[i] 
						   for i in range(len(segment.elevations)-1)]
		total_ascent = sum(change for change in elevation_changes if change > 0)
		total_descent = abs(sum(change for change in elevation_changes if change < 0))
		
		kml_str += f"""			<Placemark>
				<name><![CDATA[{segment.name}]]></name>
				<description><![CDATA[
					<div>通过"KML轨迹编辑器"生成</div>
					<div>轨迹点数:{len(segment.coordinates)}</div>
					<div>本段里程:{total_distance:.2f}米</div>
					<div>最高海拔:{max_elevation:.2f}米</div>
					<div>最低海拔:{min_elevation:.2f}米</div>
					<div>累计爬升:{total_ascent:.2f}米</div>
					<div>累计下降:{total_descent:.2f}米</div>
				]]></description>
				<styleUrl>#TbuluTrackStyle</styleUrl>
				<gx:Track>
"""
		
		# 添加坐标点
		for coord, elev in zip(segment.coordinates, segment.elevations):
			lat, lon = coord
			kml_str += f"					<gx:coord>{lon} {lat} {elev}</gx:coord>\n"
		
		kml_str += """				</gx:Track>
			</Placemark>
"""
	
	# 关闭KML文档
	kml_str += """		</Folder>
	</Document>
</kml>"""
	
	return kml_str

def render_segment_list():
	# 获取最新的轨迹段列表
	segments = st.session_state.segments
	
	# 显示轨迹段列表
	for i, segment in enumerate(segments):
		col1, col2, col3 = st.columns([1, 8, 1])
		
		# 上移按钮
		if i > 0 and col1.button("⬆️", key=f"up_{segment.order}"):
			st.session_state.segment_mgr.move_segment(segment.order, segment.order - 1)
			st.experimental_rerun()
		
		# 复选框和名称
		selected = col2.checkbox(
			segment.name,
			value=segment.selected,
			key=f"segment_{segment.order}"
		)
		
		# 下移按钮
		if i < len(segments) - 1 and col3.button("⬇️", key=f"down_{segment.order}"):
			st.session_state.segment_mgr.move_segment(segment.order, segment.order + 1)
			st.experimental_rerun()
		
		# 更新选中状态
		if selected != segment.selected:
			segment.selected = selected
			st.experimental_rerun()

def split_segment_and_update(segment):
	"""分割轨迹段并更新界面"""
	# 执行分割
	st.session_state.segment_mgr.split_segment(segment)
	
	# 重新运行应用
	st.experimental_rerun()

def main():
	st.title('轨迹编辑器')
	
	# 初始化 SegmentManager
	if 'segment_mgr' not in st.session_state:
		st.session_state.segment_mgr = SegmentManager()
	
	# 添加地图缩放级别输入框
	col1, col2 = st.columns([3, 7])
	with col1:
		new_zoom = st.number_input(
			"地图缩放级别",
			min_value=1,
			max_value=18,
			value=int(st.session_state.map_zoom) if 'map_zoom' in st.session_state else 14,
			help="设置地图缩放级别（1-18）：数字越大，显示越详细"
		)
		if 'map_zoom' not in st.session_state or new_zoom != st.session_state.map_zoom:
			st.session_state.map_zoom = new_zoom
			# 更新 URL 参数
			params = st.experimental_get_query_params()
			params['map_zoom'] = [str(new_zoom)]
			st.experimental_set_query_params(**params)
	
	# 从 URL 参数获取地图状态
	params = st.experimental_get_query_params()
	if 'map_zoom' in params:
		st.session_state.map_zoom = float(params['map_zoom'][0])
	elif 'map_zoom' not in st.session_state:
		st.session_state.map_zoom = 14
		
	if 'map_lat' in params and 'map_lon' in params:
		st.session_state.map_center = [float(params['map_lat'][0]), float(params['map_lon'][0])]
	elif 'map_center' not in st.session_state:
		st.session_state.map_center = None
		
	
	# 文件上传
	if 'has_uploaded' not in st.session_state:
		st.session_state.has_uploaded = False
	
	if not st.session_state.has_uploaded:
		uploaded_file = st.file_uploader("选择KML/KMZ文件", type=['kml', 'kmz'])
		if uploaded_file:
			try:
				coordinates, elevations = parse_kml(uploaded_file)
				if coordinates and elevations:
					st.session_state.segment_mgr.add_segment(uploaded_file.name, coordinates, elevations)
					st.session_state.has_uploaded = True
					st.experimental_rerun()
			except Exception as e:
				st.error(f"处理文件时出错：{str(e)}")
	else:
		# 添加重新上传按钮
		if st.button("重新上传文件"):
			st.session_state.has_uploaded = False
			st.experimental_rerun()

	# 获取所有轨迹段
	segments = st.session_state.segment_mgr.get_segments()
	
	if len(segments) > 0:
		# 显示轨迹段列表
		render_segment_list()
		
		# 计算地图中心点（仅在没有保存的中心点时）
		if st.session_state.map_center is None:
			all_coords = []
			for segment in segments:
				all_coords.extend(segment.coordinates)
			center_lat = sum(coord[0] for coord in all_coords) / len(all_coords)
			center_lon = sum(coord[1] for coord in all_coords) / len(all_coords)
			st.session_state.map_center = [center_lat, center_lon]
		
		# 创建地图，使用保存的状态
		m = folium.Map(
			location=st.session_state.map_center,
			zoom_start=st.session_state.map_zoom
		)
        
		# 添加自定义 JavaScript 来捕获地图状态
		js_code = """
		<script>
		// 在页面加载完成后执行
		document.addEventListener('DOMContentLoaded', function() {
			console.log('DOMContentLoaded');
			
			// 等待地图加载完成
			const waitForMap = setInterval(function() {
				console.log('Checking map...');
				const mapDiv = document.querySelector('#map');
				console.log('mapDiv:', mapDiv);
				
				if (mapDiv && mapDiv._leaflet_map) {
					console.log('Map found!');
					clearInterval(waitForMap);
					const map = mapDiv._leaflet_map;
					
					// 监听地图移动和缩放事件
					map.on('moveend zoomend', function(e) {
						console.log('Map event triggered');
						const center = map.getCenter();
						const zoom = map.getZoom();
						console.log('New center:', center);
						console.log('New zoom:', zoom);
						
						// 通过 postMessage 发送数据到父窗口
						window.parent.postMessage({
							type: 'map_state',
							center: center,
							zoom: zoom
						}, '*');
					});
					
					console.log('Event listeners set up');
				}
			}, 100);
		});
		</script>
		"""
		
		# 将 JavaScript 代码添加到地图的 head 部分
		m.get_root().header.add_child(folium.Element(js_code))
		
		# 添加监听 postMessage 的代码
		st.markdown("""
		<script>
			console.log('Parent window script loaded');
			window.addEventListener('message', function(event) {
				console.log('Received message:', event.data);
				if (event.data.type === 'map_state') {
					console.log('Map state:', event.data);
					// 更新 URL 参数
					const params = new URLSearchParams(window.location.search);
					params.set('map_zoom', event.data.zoom);
					params.set('map_lat', event.data.center.lat);
					params.set('map_lon', event.data.center.lng);
					window.history.replaceState({}, '', `${window.location.pathname}?${params}`);
					console.log('URL updated');
				}
			});
		</script>
		""", unsafe_allow_html=True)
        
		# 首先显示未选中的轨迹
		for segment in segments:
			if not segment.selected:
				folium.PolyLine(
					segment.coordinates,
					weight=3,
					color='blue',
					opacity=0.8
				).add_to(m)
        
		# 然后显示选中的轨迹
		selected_segments = [s for s in segments if s.selected]
		for segment in selected_segments:
			# 添加轨迹线
			folium.PolyLine(
				segment.coordinates,
				weight=4,
				color='red',
				opacity=1.0
			).add_to(m)
			
			# 添加起点标记
			folium.Marker(
				segment.coordinates[0],
				popup=f'{segment.name} 起点',
				icon=folium.Icon(color='green')
			).add_to(m)
			
			# 添加终点标记
			folium.Marker(
				segment.coordinates[-1],
				popup=f'{segment.name} 终点',
				icon=folium.Icon(color='red')
			).add_to(m)
			
			# 添加公里数标记
			accumulated_distance = 0
			last_marker_distance = 0
			last_point = segment.coordinates[0]
			
			for i, point in enumerate(segment.coordinates[1:], 1):
				# 计算当前点到上一个点的距离
				distance = calculate_distance(last_point, point)
				accumulated_distance += distance
				
				# 每公里添加一个标记
				if accumulated_distance - last_marker_distance >= 1000:
					# 计算实际标记位置（通过线性插值）
					overshoot = accumulated_distance - last_marker_distance - 1000
					ratio = 1 - (overshoot / distance)
					marker_lat = last_point[0] + (point[0] - last_point[0]) * ratio
					marker_lon = last_point[1] + (point[1] - last_point[1]) * ratio
					
					# 添加公里数标记
					km_number = int(accumulated_distance / 1000)
					folium.DivIcon(
						html=f'<div style="font-size: 14px; color: white; text-shadow: 1px 1px 2px black;">{km_number}km</div>',
						icon_size=(40, 20),
						icon_anchor=(20, 10)
					).add_to(folium.Marker(
						location=[marker_lat, marker_lon],
						popup=f'距起点 {km_number} 公里'
					).add_to(m))
					
					last_marker_distance = km_number * 1000
				
				last_point = point
			
			
			# 添加分裂点标记
			split_point = segment.coordinates[segment.split_point_index]
			folium.Marker(
				split_point,
				popup=f'分裂点 (点数: {segment.split_point_index + 1}/{len(segment.coordinates)})',
				icon=folium.Icon(color='orange')
			).add_to(m)
			
			# 为选中的轨迹段显示控制面板
			st.sidebar.write(f"控制面板 - {segment.name}")
			
			# 第一行：分裂点控制
			st.sidebar.write("分裂点控制：")
			col1, col2, col3, col4, col5, col6, col7 = st.sidebar.columns([1,1,1,1,1,1,1])
			if col1.button(f"⬅️10###{segment.order}"):
				st.session_state.segment_mgr.move_split_point(segment, 'backward', 10)
			if col2.button(f"⬅️6###{segment.order}"):
				st.session_state.segment_mgr.move_split_point(segment, 'backward', 6)
			if col3.button(f"⬅️3###{segment.order}"):
				st.session_state.segment_mgr.move_split_point(segment, 'backward', 3)
			if col4.button(f"✂️###{segment.order}"):
				split_segment_and_update(segment)
			if col5.button(f"➡️3###{segment.order}"):
				st.session_state.segment_mgr.move_split_point(segment, 'forward', 3)
			if col6.button(f"➡️6###{segment.order}"):
				st.session_state.segment_mgr.move_split_point(segment, 'forward', 6)
			if col7.button(f"➡️10###{segment.order}"):
				st.session_state.segment_mgr.move_split_point(segment, 'forward', 10)
			
			# 起点终点控制
			st.sidebar.write("起点终点控制：")
			sc1, sc2, sc3, sc4, sc5, sc6 = st.sidebar.columns([1,1,1,1,1,1])
			if sc1.button(f"起+10###{segment.order}"):
				st.session_state.segment_mgr.move_split_point(segment, 'start_forward', 10)
				st.experimental_rerun()
			if sc2.button(f"起+6###{segment.order}"):
				st.session_state.segment_mgr.move_split_point(segment, 'start_forward', 6)
				st.experimental_rerun()
			if sc3.button(f"起+3###{segment.order}"):
				st.session_state.segment_mgr.move_split_point(segment, 'start_forward', 3)
				st.experimental_rerun()
			if sc4.button(f"终-3###{segment.order}"):
				st.session_state.segment_mgr.move_split_point(segment, 'end_backward', 3)
				st.experimental_rerun()
			if sc5.button(f"终-6###{segment.order}"):
				st.session_state.segment_mgr.move_split_point(segment, 'end_backward', 6)
				st.experimental_rerun()
			if sc6.button(f"终-10###{segment.order}"):
				st.session_state.segment_mgr.move_split_point(segment, 'end_backward', 10)
				st.experimental_rerun()
			
			# 第二行：轨迹操作
			st.sidebar.write("轨迹操作：")
			col4, col5, col6, col7 = st.sidebar.columns(4)
			if col4.button(f"🔄 反转###{segment.order}"):
				st.session_state.segment_mgr.reverse_segment(segment)
				st.experimental_rerun()
			if col5.button(f"📋 复制###{segment.order}"):
				st.session_state.segment_mgr.duplicate_segment(segment)
				st.experimental_rerun()
			if col6.button(f"🗑️ 删除###{segment.order}"):
				st.session_state.segment_mgr.delete_segment(segment)
				st.experimental_rerun()
			if col7.button(f"✏️ 重命名###{segment.order}"):
				st.session_state.rename_segment_id = segment.order
				st.experimental_rerun()
			
			# 如果当前段处于重命名状态，显示重命名输入框
			if hasattr(st.session_state, 'rename_segment_id') and st.session_state.rename_segment_id == segment.order:
				new_name = st.sidebar.text_input(
					"输入新名称",
					value=segment.name,
					key=f"rename_input_{segment.order}"
				)
				col8, col9 = st.sidebar.columns(2)
				if col8.button(f"确认###{segment.order}"):
					segment.name = new_name
					delattr(st.session_state, 'rename_segment_id')
					st.experimental_rerun()
				if col9.button(f"取消###{segment.order}"):
					delattr(st.session_state, 'rename_segment_id')
					st.experimental_rerun()
			
        
		# 创建一个自定义组件来显示地图和处理事件
		map_html = f"""
		<div style="width:800px;height:600px;position:relative;">
			{m.get_root().render()}
			<script>
				console.log('Map component loaded');
				
				// 等待地图加载完成
				const waitForMap = setInterval(function() {{
					console.log('Checking map...');
					const mapDiv = document.querySelector('#map');
					if (mapDiv && mapDiv._leaflet_map) {{
						console.log('Map found!');
						clearInterval(waitForMap);
						const map = mapDiv._leaflet_map;
						
						// 监听地图移动和缩放事件
						map.on('moveend zoomend', function(e) {{
							console.log('Map event triggered');
							const center = map.getCenter();
							const zoom = map.getZoom();
							console.log('New center:', center);
							console.log('New zoom:', zoom);
							
							// 更新 URL 参数
							const params = new URLSearchParams(window.location.search);
							params.set('map_zoom', zoom);
							params.set('map_lat', center.lat);
							params.set('map_lon', center.lng);
							window.history.replaceState({{}}, '', `${{window.location.pathname}}?${{params}}`);
							console.log('URL updated');
						}});
						
						console.log('Event listeners set up');
					}}
				}}, 100);
			</script>
		</div>
		"""
		
		# 显示地图
		folium_static(m, width=800)
		
		# 显示选中轨迹段的基本信息
		for segment in selected_segments:
			st.write(f"基本信息 - {segment.name}：")
			st.write(f"序号：{segment.order}")
			st.write(f"总轨迹点数：{len(segment.coordinates)}个")
			st.write(f"当前分裂点位置：第 {segment.split_point_index + 1} 个点")
			st.write(f"起始海拔：{segment.elevations[0]:.1f}m")
			st.write(f"结束海拔：{segment.elevations[-1]:.1f}m")
			st.write(f"最高海拔：{max(segment.elevations):.1f}m")
			st.write(f"最低海拔：{min(segment.elevations):.1f}m")
			
			total_distance = sum(calculate_distance(segment.coordinates[i], segment.coordinates[i+1])
							  for i in range(len(segment.coordinates)-1))
			st.write(f"总距离：{total_distance/1000:.2f}km")
			
			# 计算每公里的爬升和下降
			km_stats = []
			accumulated_distance = 0
			last_km = 0
			last_point = segment.coordinates[0]
			last_elevation = segment.elevations[0]
			current_km_ascent = 0
			current_km_descent = 0
			current_km_start_distance = 0
			total_ascent = 0
			total_descent = 0
			
			for i in range(1, len(segment.coordinates)):
				point = segment.coordinates[i]
				elevation = segment.elevations[i]
				
				# 计算距离
				distance = calculate_distance(last_point, point)
				accumulated_distance += distance
				
				# 计算高度变化
				elevation_change = elevation - last_elevation
				if elevation_change > 0:
					current_km_ascent += elevation_change
					total_ascent += elevation_change
				else:
					current_km_descent += abs(elevation_change)
					total_descent += abs(elevation_change)
				
				# 如果超过1公里或是最后一个点，记录统计数据
				current_km = int(accumulated_distance / 1000)
				if current_km > last_km or i == len(segment.coordinates) - 1:
					km_distance = accumulated_distance - current_km_start_distance
					km_stats.append({
						"公里数": f"第{last_km + 1}公里",
						"实际距离": f"{km_distance:.0f}m",
						"爬升": f"{current_km_ascent:.1f}m",
						"下降": f"{current_km_descent:.1f}m"
					})
					current_km_ascent = 0
					current_km_descent = 0
					last_km = current_km
					current_km_start_distance = accumulated_distance
				
				last_point = point
				last_elevation = elevation
			
			# 添加汇总行
			km_stats.append({
				"公里数": "总计",
				"实际距离": f"{accumulated_distance:.0f}m",
				"爬升": f"{total_ascent:.1f}m",
				"下降": f"{total_descent:.1f}m"
			})
			
			# 显示统计表格
			st.write("每公里爬升下降统计：")
			st.table(km_stats)
			st.write("---")

	# 底部按钮区域
	st.write("---")
	
	# 第一行：导出按钮
	if len(segments) > 0:
		col1, col2 = st.columns(2)
		if col1.button("导出为KML", key="export_kml"):
			kml_content = export_to_kml(segments)
			# 创建下载链接
			st.download_button(
				label="点击下载KML文件",
				data=kml_content,
				file_name="exported_tracks.kml",
				mime="application/vnd.google-earth.kml+xml",
				key="download_kml"
			)
	
	# 第二行：存档相关按钮
	st.write("---")
	st.write("存档操作：")
	col3, col4 = st.columns(2)
	
	# 存档按钮（只在有轨迹时显示）
	if len(segments) > 0:
		if col3.button("保存存档", key="save_state"):
			# 生成文件名
			timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
			save_file = f"track_editor_save_{timestamp}.pkl"
			
			try:
				save_session_state(save_file)
				with open(save_file, 'rb') as f:
					st.download_button(
						label="下载存档文件",
						data=f.read(),
						file_name=save_file,
						mime="application/octet-stream",
						key="download_save"
					)
			except Exception as e:
				st.error(f"保存存档失败：{str(e)}")
	
	# 读档按钮（始终显示）
	if 'show_load_save' not in st.session_state:
		st.session_state.show_load_save = False
	
	if col4.button("加载存档", key="load_save_button"):
		st.session_state.show_load_save = not st.session_state.show_load_save
	
	if st.session_state.show_load_save:
		uploaded_save = st.file_uploader("选择存档文件", type=['pkl'], key="load_save")
		if uploaded_save is not None and 'last_uploaded_save' not in st.session_state:
			try:
				# 保存上传的文件
				with open("temp_save.pkl", "wb") as f:
					f.write(uploaded_save.getvalue())
				
				# 加载存档
				load_session_state("temp_save.pkl")
				
				# 删除临时文件
				os.remove("temp_save.pkl")
				
				# 标记已处理此文件
				st.session_state.last_uploaded_save = uploaded_save.name
				st.success("存档加载成功！")
				st.experimental_rerun()
			except Exception as e:
				st.error(f"加载存档失败：{str(e)}")
		elif uploaded_save is None and 'last_uploaded_save' in st.session_state:
			# 清除上一次上传的记录
			del st.session_state.last_uploaded_save
	
	# 第三行：清除按钮
	st.write("---")
	if st.button("清除所有轨迹", key="clear_all", type="primary"):
		st.session_state.segment_mgr.clear_segments()
		st.session_state.map_center = None
		st.session_state.map_zoom = 12
		st.session_state.next_segment_letter = 'A'  # 重置段名称
		st.experimental_set_query_params()  # 清除所有 URL 参数
		st.experimental_rerun()

if __name__ == "__main__":
    main()

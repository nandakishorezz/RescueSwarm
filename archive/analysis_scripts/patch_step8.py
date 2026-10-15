
content = open(r'c:\Users\nanda\Downloads\SURVIVOR_DETECTION\ground\frontend\src\pages\SimulationPage.tsx', encoding='utf-8').read()

# The file uses \n not \r\n internally (after the multi_replace_file_content tool changed it)
old_btn = '              <button className="primary" onClick={prepare} disabled={locked}>GENERATE WORLD</button>\n              {simStatus.state === "READY" && <button className="primary success" onClick={start}>LAUNCH MISSION</button>}\n              {locked && <button className="abort-button" onClick={stop}>STOP</button>}'

new_btn = '              <button className="primary" onClick={prepare}>GENERATE WORLD</button>\n              {simStatus.state === "READY" && <button className="primary success" onClick={start}>LAUNCH MISSION</button>}\n              {(simStatus.state === "RUNNING" || simStatus.state === "STARTING_GAZEBO" || simStatus.state === "WAITING_FOR_READY") && <button className="abort-button" onClick={stop}>STOP SIMULATION</button>}'

if old_btn in content:
    print('FOUND button block (\\n)')
    content = content.replace(old_btn, new_btn)
    old_desc = '<p>Starting Gazebo physics engine, connecting loopback telemetry, and compiling artifacts...</p>'
    new_desc = '<p>Click <b>GENERATE WORLD</b> to compile the Gazebo world. Any existing simulation is stopped automatically.</p>'
    content = content.replace(old_desc, new_desc)
    open(r'c:\Users\nanda\Downloads\SURVIVOR_DETECTION\ground\frontend\src\pages\SimulationPage.tsx', 'w', encoding='utf-8').write(content)
    print('SAVED')
else:
    # Print around the area
    idx = content.find('GENERATE WORLD')
    print('NOT FOUND with \\n either. Context:')
    print(repr(content[idx-20:idx+250]))

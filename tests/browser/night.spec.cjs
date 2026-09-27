const {test,expect}=require('@playwright/test');

async function host(page){
  await page.goto('/');
  await page.locator('#hostForm input[name=name]').fill('Host');
  await page.locator('#hostForm input[name=set]').fill('DEMO');
  await page.getByRole('button',{name:'Create prerelease',exact:true}).click();
  await expect(page.locator('.event-code')).toBeVisible();
  return page.locator('.event-code').innerText();
}
async function build(page){
  await page.getByRole('button',{name:'Open my prerelease kit'}).click();
  for(let n=1;n<=6;n++)await page.locator(`[data-pack="${n}"]`).click();
  await page.getByRole('button',{name:'Build my sealed deck'}).click();
  for(let n=0;n<23;n++)await page.locator('#poolGrid .card').first().click();
  for(let n=0;n<17;n++)await page.locator('.basic-row .plus').first().click();
  await page.getByRole('button',{name:'Save deck',exact:true}).click();
  await expect(page.locator('#saveStatus')).toContainText('Saved 40');
  await page.getByRole('button',{name:'Lobby / pairings'}).click();
}

test('two private browser seats open packs, recover, build decks and finish a tournament',async({browser,page})=>{
  const errors=[];page.on('pageerror',e=>errors.push(e.message));
  const code=await host(page);
  const context=await browser.newContext();const friend=await context.newPage();
  await friend.goto('/');await friend.locator('#joinForm input[name=name]').fill('Friend');
  await friend.locator('#joinForm input[name=code]').fill(code);
  await friend.getByRole('button',{name:'Join prerelease',exact:true}).click();
  await expect(page.locator('.player-row')).toHaveCount(2);
  await page.getByRole('button',{name:'Generate prerelease kits'}).click();
  await expect(friend.getByRole('button',{name:'Open my prerelease kit'})).toBeVisible();
  await build(page);await build(friend);
  await friend.reload();await friend.getByRole('button',{name:'Open my prerelease kit'}).click();
  await expect(friend.locator('.pack.opened')).toHaveCount(6);
  await friend.getByRole('button',{name:'Lobby',exact:true}).click();
  await page.getByRole('button',{name:'Start Round 1'}).click();
  page.on('dialog',dialog=>dialog.accept());
  for(let round=1;round<=2;round++){
    await page.getByRole('button',{name:'I won 2-0',exact:true}).click();
    const next=round===2?'Finish event':'Next round';
    await page.getByRole('button',{name:next,exact:true}).click();
  }
  await expect(page.getByRole('heading',{name:'Final standings',exact:true})).toBeVisible();
  expect(errors).toEqual([]);await context.close();
});

test('temporary server failure preserves the saved seat and can retry',async({page})=>{
  await host(page);
  const original=await page.evaluate(()=>localStorage.getItem('prerelease-night-session-v1'));
  await page.route('**/resume',route=>route.fulfill({status:503,json:{detail:'Unavailable'}}));
  await page.reload();await expect(page.getByRole('heading',{name:'Your saved seat is still here'})).toBeVisible();
  expect(await page.evaluate(()=>localStorage.getItem('prerelease-night-session-v1'))).toBe(original);
  await page.unroute('**/resume');await page.getByRole('button',{name:'Retry',exact:true}).click();
  await expect(page.locator('.event-code')).toBeVisible();
});

test('battlefield has one polling loop, preserves inputs, handles errors and stops on navigation',async({page})=>{
  await host(page);
  const session=await page.evaluate(()=>JSON.parse(localStorage.getItem('prerelease-night-session-v1')));
  const match={id:'fake-match',player_a_id:session.playerId,player_b_id:'opponent',status:'pending',engine_game_id:'table',engine_status:'active',games_a:0,games_b:0};
  let reads=0;let ended=false;let submitted;
  await page.route('**/matches/fake-match/game?**',route=>{reads++;return route.fulfill({json:{game_id:'table',decision_id:'decision',game_over:ended,result_recorded:false,match,
    state:{available:true,turn:1,players:[{is_you:true,name:'Host',hand:[],battlefield:[]},{name:'Opponent',battlefield:[]}]},
    action:{action_pending:true,response_type:'multi_amount',message:'Distribute damage',items:[{name:'Target',min:0,max:3}]}}});});
  await page.route('**/matches/fake-match/game/action',async route=>{submitted=route.request().postDataJSON();await route.fulfill({status:409,json:{detail:'Invalid target'}});});
  await page.evaluate(m=>openRulesGame(m),match);
  await expect(page.getByRole('heading',{name:'Distribute damage'})).toBeVisible();
  await page.locator('.game-multi').fill('2');await page.waitForTimeout(4000);
  await expect(page.locator('.game-multi')).toHaveValue('2');expect(reads).toBeLessThanOrEqual(4);
  await page.getByRole('button',{name:'Confirm amounts'}).click();
  await expect(page.locator('.error')).toContainText('Invalid target');expect(submitted.amounts).toEqual([2]);expect(submitted.game_id).toBe('table');
  ended=true;await page.locator('.game-multi').blur();
  await expect(page.getByRole('heading',{name:'Game complete',exact:true})).toBeVisible();
  await page.getByRole('button',{name:'Pairings / sideboard'}).click();
  const before=reads;await page.waitForTimeout(2200);expect(reads).toBe(before);
});

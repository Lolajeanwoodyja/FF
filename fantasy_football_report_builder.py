# written by Wren J.R.
# contributors: Kevin N.
# code snippets taken from: http://tech.thejoestory.com/2014/12/yahoo-fantasy-football-api-using-python.html

import collections
import datetime
import itertools
import os
from ConfigParser import ConfigParser

import yql
from yql.storage import FileTokenStore

from metrics import PointsByPosition, SeasonAverageCalculator, Breakdown, CalculateMetrics, PowerRanking, ZScore
from pdf_generator import PdfGenerator


# noinspection SqlNoDataSourceInspection,SqlDialectInspection
class FantasyFootballReport(object):
    def __init__(self, user_input_league_id=None, user_input_chosen_week=None, test_bool=False):
        # config vars
        self.config = ConfigParser()
        self.config.read("config.ini")
        if user_input_league_id:
            self.league_id = user_input_league_id
        else:
            self.league_id = self.config.get("Fantasy_Football_Report_Settings", "chosen_league_id")

        self.test_bool = False
        if test_bool:
            self.test_bool = True
            print("\nGenerating test report...\n")

        # verification output message
        print("Generating fantasy football report for league with id: %s (report generated: %s)\n" % (
            self.league_id, "{:%Y-%b-%d %H:%M:%S}".format(datetime.datetime.now())))

        # yahoo oauth api (consumer) key and secret
        with open("./authentication/private.txt", "r") as auth_file:
            auth_data = auth_file.read().split("\n")
        consumer_key = auth_data[0]
        consumer_secret = auth_data[1]

        # yahoo oauth process
        self.y3 = yql.ThreeLegged(consumer_key, consumer_secret)
        _cache_dir = self.config.get("OAuth_Settings", "yql_cache_dir")
        if not os.access(_cache_dir, os.R_OK):
            os.mkdir(_cache_dir)

        token_store = FileTokenStore(_cache_dir, secret="sasfasdfdasfdaf")
        stored_token = token_store.get("foo")

        if not stored_token:
            request_token, auth_url = self.y3.get_token_and_auth_url()
            print("Visit url %s and get a verifier string" % auth_url)
            verifier = raw_input("Enter the code: ")
            self.token = self.y3.get_access_token(request_token, verifier)
            token_store.set("foo", self.token)

        else:
            print("Verifying token...\n")
            self.token = self.y3.check_token(stored_token)
            if self.token != stored_token:
                print("Setting stored token!\n")
                token_store.set("foo", self.token)

        '''
        run base yql queries
        '''
        # get fantasy football game info
        game_data = self.yql_query("select * from fantasysports.games where game_key='nfl'")
        # unique league key composed of this year's yahoo fantasy football game id and the unique league id
        self.league_key = game_data[0].get("game_key") + ".l." + self.league_id
        print("League key: %s\n" % self.league_key)

        # get individual league roster
        roster_data = self.yql_query(
            "select * from fantasysports.leagues.settings where league_key='" + self.league_key + "'")

        roster_slots = collections.defaultdict(int)
        self.league_roster_active_slots = []
        flex_positions = []

        for position in roster_data[0].get("settings").get("roster_positions").get("roster_position"):

            position_name = position.get("position")
            position_count = int(position.get("count"))

            count = position_count
            while count > 0:
                if position_name != "BN":
                    self.league_roster_active_slots.append(position_name)
                count -= 1

            if position_name == "W/R":
                flex_positions = ["WR", "RB"]
            if position_name == "W/R/T":
                flex_positions = ["WR", "RB", "TE"]

            if "/" in position_name:
                position_name = "FLEX"

            roster_slots[position_name] += position_count

        self.roster = {
            "slots": roster_slots,
            "flex_positions": flex_positions
        }

        # get data for all teams in league
        self.teams_data = self.yql_query("select * from fantasysports.teams where league_key='" + self.league_key + "'")

        # get data for all league standings
        self.league_standings_data = self.yql_query(
            "select * from fantasysports.leagues.standings where league_key='" + self.league_key + "'")
        self.league_name = self.league_standings_data[0].get("name")
        # TODO: incorporate winnings into reports
        # entry_fee = league_standings_data[0].get("entry_fee")

        # user input validation
        if user_input_chosen_week:
            chosen_week = user_input_chosen_week
        else:
            chosen_week = self.config.get("Fantasy_Football_Report_Settings", "chosen_week")
        try:
            if chosen_week == "default":
                self.chosen_week = str(int(self.league_standings_data[0].get("current_week")) - 1)
            elif 0 < int(chosen_week) < 18:
                if 0 < int(chosen_week) <= int(self.league_standings_data[0].get("current_week")) - 1:
                    self.chosen_week = chosen_week
                else:
                    incomplete_week = raw_input(
                        "Are you sure you want to generate a report for an incomplete week? (y/n) -> ")
                    if incomplete_week == "y":
                        self.chosen_week = chosen_week
                    elif incomplete_week == "n":
                        raise ValueError("It is recommended that you not generate a report for an incomplete week.")
                    else:
                        raise ValueError("Please only select 'y' or 'n'. Try running the report generator again.")
            else:
                raise ValueError("You must select either 'default' or an integer from 1 to 17 for the chosen week.")
        except ValueError:
            raise ValueError("You must select either 'default' or an integer from 1 to 17 for the chosen week.")

    def yql_query(self, query):
        # print("Executing query: %s\n" % query)
        return self.y3.execute(query, token=self.token).rows

    def retrieve_scoreboard(self, chosen_week):
        """
        get weekly matchup data
        
        result format is like: 
        [
            {
                'team1': { 
                    'result': 'W',
                    'score': 100
                },
                'team2': {
                    'result': 'L',
                    'score': 50
                }
            },
            {
                'team3': {
                    'result': 'T',
                    'score': 75
                },
                'team4': {
                    'result': 'T',
                    'score': 75
                }371.l.52364
            }
        ]
        """
        result = self.yql_query(
            "select * from fantasysports.leagues.scoreboard where league_key='{0}' and week='{1}'".format(
                self.league_key, chosen_week))

        matchups = result[0].get("scoreboard").get("matchups").get("matchup")

        matchup_list = []

        for matchup in matchups:
            winning_team = matchup.get("winner_team_key")
            is_tied = int(matchup.get("is_tied"))

            def team_result(team):
                """
                determine if team tied/won/lost
                """
                team_key = team.get("team_key")

                if is_tied:
                    return "T"

                return "W" if team_key == winning_team else "L"

            teams = {
                team.get("name").encode("utf-8"): {
                    "result": team_result(team),
                    "score": team.get("team_points").get("total")
                } for team in matchup.get("teams").get("team")
            }

            matchup_list.append(teams)

        return matchup_list

    def retrieve_data(self, chosen_week):

        teams_dict = {}
        for team in self.teams_data:

            team_id = team.get("team_id")
            team_name = team.get("name")
            team_managers = team.get("managers").get("manager")

            team_manager = ""
            if type(team_managers) is dict:
                team_manager = team_managers.get("nickname")
            else:
                for manager in team_managers:
                    if manager.get("is_comanager") is None:
                        team_manager = manager.get("nickname")

            team_info_dict = {"name": team_name, "manager": team_manager}
            teams_dict[team_id] = team_info_dict

        # output league teams for verification
        if chosen_week == "1":
            print("TEAMS: {}\n".format(teams_dict))
            print("Generating report for week {}\n".format(self.chosen_week))

        team_results_dict = {}

        # iterate through all teams and build team_results_dict containing all relevant team stat information
        for team in teams_dict:

            team_id = team
            team_name = teams_dict.get(team).get("name").encode("utf-8")

            # get data for this individual team
            roster_stats_data = self.yql_query(
                "select * from fantasysports.teams.roster.stats where team_key='" + self.league_key + ".t." + team + "' and week='" + chosen_week + "'")

            players = []
            positions_filled_active = []
            for player in roster_stats_data[0].get("roster").get("players").get("player"):
                player_selected_position = player.get("selected_position").get("position")

                if player_selected_position != "BN":
                    positions_filled_active.append(player_selected_position)

                player_info_dict = {"name": player.get("name")["full"],
                                    "status": player.get("status"),
                                    "bye_week": int(player.get("bye_weeks")["week"]),
                                    "selected_position": player.get("selected_position").get("position"),
                                    "eligible_positions": player.get("eligible_positions").get("position"),
                                    "fantasy_points": float(player.get("player_points").get("total", 0.0))}

                players.append(player_info_dict)

            # for testing ties
            # test_score = 70
            # if int(team_id) == 1:
            #     test_score = 100
            # if int(team_id) == 2:
            #     test_score = 100
            # if int(team_id) == 3:
            #     test_score = 100
            # if int(team_id) == 4:
            #     test_score = 90
            # if int(team_id) == 5:
            #     test_score = 90
            # if int(team_id) == 6:
            #     test_score = 90

            team_results_dict[team_name] = {
                "name": team_name,
                "manager": teams_dict.get(team).get("manager"),
                "players": players,
                "score": sum([p["fantasy_points"] for p in players if p["selected_position"] != "BN"]),
                # "score": test_score,
                "bench_score": sum([p["fantasy_points"] for p in players if p["selected_position"] == "BN"]),
                "team_id": team_id,
                "positions_filled_active": positions_filled_active
            }

        return team_results_dict

    def calculate_metrics(self, weekly_team_info, chosen_week):

        matchups_list = self.retrieve_scoreboard(chosen_week)
        team_results_dict = self.retrieve_data(chosen_week)

        # get current standings
        calculate_metrics = CalculateMetrics(self.league_id, self.config)
        current_standings_data = calculate_metrics.get_standings(self.league_standings_data)

        # calculate coaching efficiency metric and add values to team_results_dict, and get and points by position
        points_by_position = PointsByPosition(self.roster)
        weekly_points_by_position_data = points_by_position.get_weekly_points_by_position(self.league_id, self.config,
                                                                                          chosen_week, self.roster,
                                                                                          self.league_roster_active_slots,
                                                                                          team_results_dict)

        breakdown = Breakdown()
        breakdown_results = breakdown.execute_breakdown(team_results_dict, matchups_list)

        # yes, this is kind of redundent but its clearer that the individual metrics
        # are _not_ supposed to be modifying the things passed into it
        for team_id in team_results_dict:
            team_results_dict[team_id]["luck"] = breakdown_results[team_id]["luck"] * 100
            team_results_dict[team_id]["breakdown"] = breakdown_results[team_id]["breakdown"]

        # dependent on all previous weeks scores
        zscore = ZScore()
        zscore_results = zscore.execute(weekly_team_info + [team_results_dict])

        for team_id in team_results_dict:
            team_results_dict[team_id]["zscore"] = zscore_results[team_id]

        power_ranking_metric = PowerRanking()
        power_ranking_results = power_ranking_metric.execute_power_ranking(team_results_dict)

        for team_id in team_results_dict:
            team_results_dict[team_id]["power_rank"] = power_ranking_results[team_id]["power_rank"]
            team_results_dict[team_id]["zscore_rank"] = power_ranking_results[team_id]["zscore_rank"]


        # used only for testing what happens when different metrics are tied; requires uncommenting lines in method
        if self.test_bool:
            calculate_metrics.test_ties(team_results_dict)

        # create power ranking data for table
        power_ranking_results = sorted(team_results_dict.iteritems(), key=lambda (k, v): v["power_rank"])
        power_ranking_results_data = []
        for key, value in power_ranking_results:
            # season avg calc does something where it keys off the second value in the array
            # WREN WHYYYYY
            power_ranking_results_data.append(
                [value.get("power_rank"), key, value.get("manager")]
            )

        # create zscore data for table
        zscore_results = sorted(team_results_dict.iteritems(), key=lambda (k, v): v["zscore"], reverse=True)
        zscore_results_data = []
        for key, value in zscore_results:
            valid = value.get("zscore") is not None
            zscore_rank = value.get("zscore_rank") if valid else "N/A"
            zscore_value = "%.2f" % value.get("zscore") if valid else "N/A"

            zscore_results_data.append(
                [zscore_rank, key, value.get("manager"),  zscore_value]
            )


        # create score data for table
        score_results = sorted(team_results_dict.iteritems(),
                               key=lambda (k, v): (float(v.get("score")), k), reverse=True)
        score_results_data = calculate_metrics.get_score_data(score_results)

        # create coaching efficiency data for table
        coaching_efficiency_results = sorted(team_results_dict.iteritems(),
                                             key=lambda (k, v): (v.get("coaching_efficiency"), k),
                                             reverse=True)
        efficiency_dq_count = 0
        coaching_efficiency_results_data = calculate_metrics.get_coaching_efficiency_data(
            coaching_efficiency_results, efficiency_dq_count)

        # create luck data for table
        luck_results = sorted(team_results_dict.iteritems(),
                              key=lambda (k, v): (v.get("luck"), k), reverse=True)
        luck_results_data = calculate_metrics.get_luck_data(luck_results)

        # count number of ties for points, coaching efficiency, and luck
        # tie_type can be "score", "coaching_efficiency", "luck", or "power_rank"
        num_tied_scores = calculate_metrics.get_num_ties(score_results_data, chosen_week,
                                                         tie_type="score")
        # reorder score data based on bench points
        if num_tied_scores > 0:
            score_results_data = calculate_metrics.resolve_score_ties(score_results_data)
            calculate_metrics.get_num_ties(score_results_data, chosen_week, tie_type="score")
        tie_for_first_score = False
        if score_results_data[0][0] == score_results_data[1][0]:
            tie_for_first_score = True
        num_tied_for_first_scores = len(
            [list(group) for key, group in itertools.groupby(score_results_data, lambda x: x[3])][0])

        num_tied_coaching_efficiencies = calculate_metrics.get_num_ties(coaching_efficiency_results_data, chosen_week,
                                                                        tie_type="coaching_efficiency")
        tie_for_first_coaching_efficiency = False
        if coaching_efficiency_results_data[0][0] == coaching_efficiency_results_data[1][0]:
            tie_for_first_coaching_efficiency = True
        num_tied_for_first_coaching_efficiency = len(
            [list(group) for key, group in itertools.groupby(coaching_efficiency_results_data, lambda x: x[3])][0])

        num_tied_lucks = calculate_metrics.get_num_ties(luck_results_data, chosen_week, tie_type="luck")
        tie_for_first_luck = False
        if luck_results_data[0][0] == luck_results_data[1][0]:
            tie_for_first_luck = True
        num_tied_for_first_luck = len(
            [list(group) for key, group in itertools.groupby(luck_results_data, lambda x: x[3])][0])

        num_tied_power_rankings = calculate_metrics.get_num_ties(power_ranking_results_data,
                                                                 chosen_week, tie_type="power_rank")
        tie_for_first_power_ranking = False
        if power_ranking_results_data[0][0] == power_ranking_results_data[1][0]:
            tie_for_first_power_ranking = True
        num_tied_for_first_power_ranking = len(
            [list(group) for key, group in itertools.groupby(power_ranking_results_data, lambda x: x[0])][0])

        

        report_info_dict = {
            "team_results": team_results_dict,
            "current_standings_data": current_standings_data,
            "score_results_data": score_results_data,
            "coaching_efficiency_results_data": coaching_efficiency_results_data,
            "luck_results_data": luck_results_data,
            "power_ranking_results_data": power_ranking_results_data,
            "zscore_results_data": zscore_results_data,
            "num_tied_scores": num_tied_scores,
            "num_tied_coaching_efficiencies": num_tied_coaching_efficiencies,
            "num_tied_lucks": num_tied_lucks,
            "num_tied_power_rankings": num_tied_power_rankings,
            "efficiency_dq_count": efficiency_dq_count,
            "tied_scores_bool": num_tied_scores > 0,
            "tied_coaching_efficiencies_bool": num_tied_coaching_efficiencies > 0,
            "tied_lucks_bool": num_tied_lucks > 0,
            "tied_power_rankings_bool": num_tied_power_rankings > 0,
            "tie_for_first_score": tie_for_first_score,
            "tie_for_first_coaching_efficiency": tie_for_first_coaching_efficiency,
            "tie_for_first_luck": tie_for_first_luck,
            "tie_for_first_power_ranking": tie_for_first_power_ranking,
            "num_tied_for_first_scores": num_tied_for_first_scores,
            "num_tied_for_first_coaching_efficiency": num_tied_for_first_coaching_efficiency,
            "num_tied_for_first_luck": num_tied_for_first_luck,
            "num_tied_for_first_power_ranking": num_tied_for_first_power_ranking,
            "weekly_points_by_position_data": weekly_points_by_position_data
        }

        return report_info_dict

    def create_pdf_report(self):

        chosen_week_ordered_team_names = []
        chosen_week_ordered_managers = []
        report_info_dict = {}

        weekly_team_info = []

        time_series_points_data = []
        time_series_efficiency_data = []
        time_series_luck_data = []
        time_series_power_rank_data = []
        time_series_zscore_data = []

        season_average_points_by_position_dict = collections.defaultdict(list)

        week_counter = 1
        while week_counter <= int(self.chosen_week):
            report_info_dict = self.calculate_metrics(weekly_team_info, chosen_week=str(week_counter))

            weekly_team_info.append(report_info_dict.get('team_results'))

            # for id, team in report_info_dict.get("team_results").items():
            #     scores_by_week[id].append(team.get('score'))

            # create team data for charts
            teams_data_list = []
            team_results = report_info_dict.get("team_results")
            for team in team_results:
                temp_team_info = team_results.get(team)
                teams_data_list.append([
                    temp_team_info.get("team_id"),
                    team,
                    temp_team_info.get("manager"),
                    temp_team_info.get("score"),
                    temp_team_info.get("coaching_efficiency"),
                    temp_team_info.get("luck"),
                    temp_team_info.get("power_rank"),
                    temp_team_info.get("zscore")
                ])

                points_by_position = report_info_dict.get("weekly_points_by_position_data")
                for weekly_info in points_by_position:
                    if weekly_info[0] == team:
                        season_average_points_by_position_dict[team].append(weekly_info[1])

                

            teams_data_list.sort(key=lambda x: int(x[0]))

            ordered_team_names = []
            ordered_team_managers = []
            weekly_points_data = []
            weekly_coaching_efficiency_data = []
            weekly_luck_data = []
            weekly_power_rank_data = []
            weekly_zscore_data = []

            for team in teams_data_list:
                ordered_team_names.append(team[1])
                ordered_team_managers.append(team[2])
                weekly_points_data.append([int(week_counter), float(team[3])])
                weekly_coaching_efficiency_data.append([int(week_counter), team[4]])
                weekly_luck_data.append([int(week_counter), float(team[5])])
                weekly_power_rank_data.append([int(week_counter), team[6]])
                weekly_zscore_data.append([int(week_counter), team[7]])

            chosen_week_ordered_team_names = ordered_team_names
            chosen_week_ordered_managers = ordered_team_managers

            if week_counter == 1:
                for team_points in weekly_points_data:
                    time_series_points_data.append([team_points])
                for team_efficiency in weekly_coaching_efficiency_data:
                    time_series_efficiency_data.append([team_efficiency])
                for team_luck in weekly_luck_data:
                    time_series_luck_data.append([team_luck])
                for team_power_rank in weekly_power_rank_data:
                    time_series_power_rank_data.append([team_power_rank])
                for team_zscore in weekly_zscore_data:
                    time_series_zscore_data.append([team_zscore])
            else:
                for index, team_points in enumerate(weekly_points_data):
                    time_series_points_data[index].append(team_points)
                for index, team_efficiency in enumerate(weekly_coaching_efficiency_data):
                    if float(team_efficiency[1]) != 0.0:
                        time_series_efficiency_data[index].append(team_efficiency)
                for index, team_luck in enumerate(weekly_luck_data):
                    time_series_luck_data[index].append(team_luck)
                for index, team_power_rank in enumerate(weekly_power_rank_data):
                    time_series_power_rank_data[index].append(team_power_rank)
                for index, team_zscore in enumerate(weekly_zscore_data):
                    time_series_zscore_data[index].append(team_zscore)
            week_counter += 1

        # calculate season average metrics and then add columns for them to their respective metric table data
        season_average_calculator = SeasonAverageCalculator(chosen_week_ordered_team_names, report_info_dict)

        report_info_dict["score_results_data"] = season_average_calculator.get_average(
            time_series_points_data, "score_results_data", with_percent_bool=False)
        report_info_dict["coaching_efficiency_results_data"] = season_average_calculator.get_average(
            time_series_efficiency_data, "coaching_efficiency_results_data", with_percent_bool=True)
        report_info_dict["luck_results_data"] = season_average_calculator.get_average(time_series_luck_data,
                                                                                      "luck_results_data",
                                                                                      with_percent_bool=True)
        report_info_dict["power_ranking_results_data"] = season_average_calculator.get_average(
            time_series_power_rank_data, "power_ranking_results_data", with_percent_bool=False, bench_column_bool=False,
            reverse_bool=False)

        report_info_dict["zscore_results_data"] = season_average_calculator.get_average(
            time_series_zscore_data, "zscore_results_data", with_percent_bool=False, bench_column_bool=False,
            reverse_bool=False)

        line_chart_data_list = [chosen_week_ordered_team_names,
                                chosen_week_ordered_managers,
                                time_series_points_data,
                                time_series_efficiency_data,
                                time_series_luck_data,
                                time_series_zscore_data,
                                time_series_power_rank_data]

        # calculate season average points by position and add them to the report_info_dict
        PointsByPosition.calculate_points_by_position_season_averages(season_average_points_by_position_dict,
                                                                      report_info_dict)

        filename = self.league_name.replace(" ",
                                            "-") + "(" + self.league_id + ")_week-" + self.chosen_week + "_report.pdf"
        report_save_dir = self.config.get("Fantasy_Football_Report_Settings",
                                          "report_directory_base_path") + "/" + self.league_name.replace(" ",
                                                                                                         "-") + "(" + self.league_id + ")"
        report_title_text = self.league_name + " (" + self.league_id + ") Week " + self.chosen_week + " Report"
        report_footer_text = "Report generated %s for Yahoo Fantasy Football league '%s' (%s)." % (
            "{:%Y-%b-%d %H:%M:%S}".format(datetime.datetime.now()), self.league_name, self.league_id)

        print("Filename: {}\n".format(filename))

        if not os.path.isdir(report_save_dir):
            os.makedirs(report_save_dir)

        if not self.test_bool:
            filename_with_path = os.path.join(report_save_dir, filename)
        else:
            filename_with_path = os.path.join(
                self.config.get("Fantasy_Football_Report_Settings", "report_directory_base_path") + "/",
                "test_report.pdf")

        # instantiate pdf generator
        pdf_generator = PdfGenerator(
            league_id=self.league_id,
            report_title_text=report_title_text,
            standings_title_text="League Standings",
            scores_title_text="Team Score Rankings",
            zscores_title_text="Team Z-Score Rankings",
            coaching_efficiency_title_text="Team Coaching Efficiency Rankings",
            luck_title_text="Team Luck Rankings",
            power_ranking_title_text="Team Power Rankings",
            report_footer_text=report_footer_text,
            report_info_dict=report_info_dict
        )

        # generate pdf of report
        file_for_upload = pdf_generator.generate_pdf(filename_with_path, line_chart_data_list)

        print("Generated PDF: {}\n".format(file_for_upload))

        return file_for_upload
